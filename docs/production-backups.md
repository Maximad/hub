# Production backup and restore runbook

The VPS backup covers the PostgreSQL database and `/opt/hub/media`. It writes an
atomic backup under `/opt/hub/backups/production`, including `database.sql`,
`media.tar.gz`, `counts.tsv`, `manifest.txt`, `SHA256SUMS`, and a `SUCCESS` marker.
The web container is paused while the database and media snapshot is captured,
and an exit trap unpauses it if any backup step fails.
The manifest contains only timestamps, the Git commit, sizes, and basic counts;
it contains no database password or application secret. Deployment and backup
share a non-blocking lock, and deployment runs the full backup before updating.

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
