# Production backup and restore runbook

The VPS backup covers the PostgreSQL database and `/opt/hub/media`. It writes an
atomic backup under `/opt/hub/backups/production`, including `database.sql`,
`media.tar.gz`, `counts.tsv`, `manifest.txt`, `SHA256SUMS`, and a `SUCCESS` marker.
The web container is paused while the database and media snapshot is captured,
and an exit trap unpauses it if any backup step fails.
The manifest contains only timestamps, the Git commit, sizes, and basic counts;
it contains no database password or application secret. Deployment and backup
share a non-blocking lock, and deployment runs the full backup before updating.

## One-time timestamp-parser deployment bootstrap

Production commit `51249a4` cannot deploy the parser correction in `6d14032` by
running `deploy-production.sh`. The normal ordering is: check the old checkout,
fetch `origin/main`, create a backup **with scripts from the old checkout**, run
the old retention parser, and only then fast-forward the checkout. The legacy
parser passes `YYYYMMDDTHHMMSS` to GNU `date` in an invalid form, retention exits,
the deploy error trap stops the deployment, and the source-update step is never
reached. Re-running follows exactly the same ordering and fails again.

The one-time bridge verifies (rather than deletes, renames, or skips) the newest
existing full backup before changing the checkout. It then validates that
`origin/main` is a commit and a fast-forward, records the old HEAD in
`backups/rollback_revision.txt`, and fast-forwards the working tree. No Docker
command is run before that fast-forward, so the running containers remain on the
old image. The corrected `deploy-production.sh` re-verifies the same backup at
handoff, builds the image, runs checks, replaces the web container, migrates,
collects static files, restarts, and performs the normal smoke, audit, route, and
log checks. It writes `backups/last_deployed_revision.txt` only after all checks
succeed; therefore that file continues to describe the last successful deploy.

Run these exact commands as the `deploy` user (not root):

```bash
cd /opt/hub
test "$(git rev-parse HEAD)" = 51249a4917ccbfc935f793332510813636694d9d
test -z "$(git status --porcelain)"
git fetch origin main
git show origin/main:scripts/bootstrap-production-deploy.sh > /tmp/bootstrap-production-deploy.sh
chmod 700 /tmp/bootstrap-production-deploy.sh
/tmp/bootstrap-production-deploy.sh
rm -f /tmp/bootstrap-production-deploy.sh
cat backups/rollback_revision.txt
cat backups/last_deployed_revision.txt
test "$(cat backups/last_deployed_revision.txt)" = "$(git rev-parse HEAD)"
```

If independently confirming the full production commit, compare it with
`git rev-parse 51249a4`; do not continue on a mismatch. Do not create, remove,
rename, or edit anything under `backups/production` during this procedure. The verifier restores into an
isolated temporary PostgreSQL container and never changes production data.

If the bootstrap deployment fails, preserve its output and the verified backup.
Do not reset volumes or run reverse migrations. The following source/image
rollback returns the application code to the accurately recorded revision while
leaving production data and every backup intact:

```bash
cd /opt/hub
rollback="$(cat backups/rollback_revision.txt)"
git cat-file -e "$rollback^{commit}"
git checkout --detach "$rollback"
docker compose -f docker-compose.prod.yml --env-file .env build web
docker compose -f docker-compose.prod.yml --env-file .env up -d --no-deps --force-recreate web
docker compose -f docker-compose.prod.yml --env-file .env exec -T web python manage.py collectstatic --noinput --clear
docker compose -f docker-compose.prod.yml --env-file .env restart web
curl -kfsS https://hubsweida.jwtalenthouse.com/menu/ >/dev/null
docker compose -f docker-compose.prod.yml --env-file .env exec -T web python manage.py check
docker compose -f docker-compose.prod.yml --env-file .env exec -T web python manage.py smoke_check
```

Escalate for a migration-aware recovery if old application code is not compatible
with already-applied forward migrations; never reset production data. After a
successful bootstrap, leave the checkout on `main`. All later deployments use
the ordinary `./scripts/deploy-production.sh` path, which creates a fresh full
backup and runs the corrected strict UTC timestamp parser before updating source.

## Schedule

As `deploy`, edit `crontab -e` and install (02:17 UTC daily):

```cron
17 2 * * * cd /opt/hub && ./scripts/backup-production.sh >>/var/log/hub-backup.log 2>&1
```

The default retention is 14 days. Override it with `RETENTION_DAYS=30`; only
validated, direct child backup directories are eligible, and the newest
successful backup is always retained. Monitor the cron exit status/log and disk
space. These are **local backups only**: off-site backup is not enabled.

After a successful run, a later upload job may copy the completed directory to
an encrypted, access-controlled object store. The hook must run only when
`SUCCESS` exists, verify `SHA256SUMS` after transfer, use credentials supplied by
the server's secret manager (never this repository), and alert on failure.

## Verify and restore drill

Run after every backup or on a separate schedule:

```bash
latest="$(find /opt/hub/backups/production -mindepth 1 -maxdepth 1 -type d -name 'hub-*' -exec test -f '{}/SUCCESS' \; -print | sort | tail -1)"
/opt/hub/scripts/verify-production-backup.sh "$latest"
```

Verification checks every checksum and the media archive before starting an
isolated `postgres:16` container. SQL is restored with `ON_ERROR_STOP`, the table
set is compared with the captured counts, and a trap removes the container even
when verification fails. It never connects to or modifies production.

## Emergency restore

Stop application writes first. Verify the selected backup, save the damaged
database volume rather than deleting it immediately, then restore into a newly
created PostgreSQL 16 database:

```bash
backup=/opt/hub/backups/production/hub-YYYYMMDDTHHMMSSZ
/opt/hub/scripts/verify-production-backup.sh "$backup"
cd /opt/hub
docker compose -f docker-compose.prod.yml --env-file .env stop web
# Create/select a clean recovery database, then restore with fail-fast SQL:
docker compose -f docker-compose.prod.yml --env-file .env exec -T db \
  psql -X -U "$POSTGRES_USER" -d hub_recovery -v ON_ERROR_STOP=1 <"$backup/database.sql"
tar -xzf "$backup/media.tar.gz" -C /opt/hub
```

Do not overwrite the live database until the isolated verification succeeds and
the incident owner has approved the cutover. Keep the pre-incident volume and
backup immutable until application smoke checks and essential record counts pass.
