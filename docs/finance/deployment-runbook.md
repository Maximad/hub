# Finance ledger deployment runbook

This runbook is the production procedure for an **additive** finance-ledger
release. Copy the command output, UTC timestamps, commit SHA, database backup
name, row counts, approvals, and incident links into the deployment record.
Commands are run from `/opt/hub` by the `deploy` user unless stated otherwise.

## Non-negotiable safety rules

1. Posting batches, posting entries, posting commands, reconciliation failures,
   finance review items, cash/stock projections, and audit events are financial
   history. **Never delete, truncate, overwrite, or reverse them by SQL during a
   deploy or rollback.** Corrections use the supported posting reversal flow and
   create new audit history.
2. This release may only add nullable fields, tables, indexes, constraints, or
   compatible code. A migration that drops a financial field, makes an old
   field unreadable, or rewrites existing financial values is a **separate later
   release**. Schedule it only after the new release has completed at least one
   full retention period with successful daily reconciliation. The retention
   period is the organization's configured financial-record retention period;
   if no period is formally configured, no destructive/rewrite migration is
   authorized.
3. Application rollback means: stop new posting first, return every read to the
   compatible legacy projection, and retain all new and old records. Do not run
   reverse migrations that remove ledger schema or data.
4. Do not edit a discrepancy until its evidence is captured. Never “fix” a
   mismatch by changing a posted amount in place.

## Roles and authorization

| Role | Responsibility |
| --- | --- |
| Deployment operator | Runs commands, records evidence, and stops on a failed gate. Cannot self-authorize cutover. |
| Database operator | Reviews migration locks/query plans; creates and verifies the backup and restore drill. |
| Finance owner | Confirms business totals, review-item disposition, close status, and reconciliation acceptance. |
| Engineering incident commander (IC) | Owns the change window, technical go/no-go, monitoring, and rollback. |

Only the **finance owner and engineering IC together** can authorize enabling
dual reads or report cutover. Record both names and UTC approval times. The
database operator must additionally approve any migration expected to lock a
financial table. Any one of the finance owner, engineering IC, database
operator, or deployment operator may call a stop or rollback; resuming still
requires finance-owner and IC approval. Emergency disabling of posting needs no
prior approval.

## Fixed acceptance thresholds

These are gates, not targets:

- `reconcile_postings`: zero newly detected bypasses and zero unresolved
  `PostingReconciliationFailure` rows.
- `reconcile_finance --check`: zero findings for cutover. Monetary, ledger
  balance, transfer-leg, and projection tolerances are exactly **0 SYP**;
  quantity tolerance is exactly the stored database precision (no additional
  operational rounding tolerance).
- Backfill: zero failed batches and zero unexplained/skipped records. A record
  requiring judgment remains a `FinanceReviewItem`; it is not auto-matched.
- HTTP 5xx rate: no increase above the pre-deploy baseline and never more than
  1% over a five-minute window. Posting errors, duplicate/idempotency errors,
  unbalanced batches, and new reconciliation discrepancies: zero.
- Database lock wait: 5 seconds maximum. A migration or backfill batch taking
  more than 5 minutes is interrupted and investigated; the overall command has
  a hard 10-minute timeout.
- Reports remain on legacy reads until one complete business-day close (the
  minimum observation period) has reconciled at the thresholds above. The
  longer configured retention period still applies before any field removal or
  rewrite.

## 1. Pre-deploy (T-7 days through T-30 minutes)

### Change and migration review

- [ ] Name the deployment operator, database operator, finance owner, and IC;
  announce the window and freeze unrelated finance changes.
- [ ] Confirm the target and rollback SHAs, image digest, previous compatible
  image, `.env` owner/mode, free database/disk space, and backup retention.
- [ ] Review every migration between the deployed and target SHAs. Attach
  `python manage.py showmigrations --plan` output. Reject this release if any
  operation removes/renames a legacy finance field, changes its meaning, or
  rewrites existing values.
- [ ] For each SQL operation, review `sqlmigrate`, estimated affected rows,
  transaction behavior, index creation method, expected lock and duration, and
  reverse behavior. Use a production-sized staging clone. Additive migrations
  must work with both the old and new application versions.
- [ ] Run the posting, finance, and migration tests and confirm that disabling
  `POSTING_LEDGER_WRITES_ENABLED` forces legacy reads.

Use the target image rather than the currently running container for the plan:

```bash
set -o pipefail
export DC='docker compose -f docker-compose.prod.yml --env-file .env'
$DC run --rm -T web python manage.py check
$DC run --rm -T web python manage.py makemigrations --check --dry-run
$DC run --rm -T web python manage.py showmigrations --plan | tee deployment-showmigrations.txt
# Repeat for each new migration, substituting its app and migration name:
$DC run --rm -T web python manage.py sqlmigrate core 0031 | tee deployment-0031.sql
```

### Baseline and safe initial flags

Set deployment overrides before replacing the application. Overrides take
precedence over database flags. Start with all rollout phases off:

```dotenv
POSTING_LEDGER_WRITES_ENABLED=False
POSTING_DUAL_READ_ENABLED=False
POSTING_REPORT_READS_ENABLED=False
```

Recreate the web container after every `.env` flag change and verify the
effective values from the application, not merely from the file:

```bash
$DC up -d --no-deps --force-recreate web
$DC exec -T web python manage.py shell -c \
  "from core.services.posting.rollout import current_rollout; print(current_rollout())"
```

Capture a 30-minute baseline: request/5xx rate, posting latency/error count,
database CPU/connections/locks/replication lag, job backlog, and legacy daily
totals by account and business date. Then run read-only checks:

```bash
$DC exec -T web python manage.py reconcile_postings
$DC exec -T web python manage.py reconcile_finance --scope=expenses --check --format=json
$DC exec -T web python manage.py reconcile_finance --check --format=json \
  | tee reconciliation-before.json
```

Both must meet the fixed thresholds. Existing findings block deployment unless
the finance owner documents them and the IC moves the release back to planning;
documented exceptions do **not** permit report cutover under this runbook.

## 2. Backup and restore verification (T-30 minutes)

The deploy script makes a plain SQL backup, but a successful `pg_dump` alone is
not verification. Record the exact database name/user from `.env`; the examples
below assume `hub`/`hub` as used by the production deploy script.

1. Quiesce finance-changing workers/integrations and prevent operator posting.
   Record the last committed posting/audit IDs and UTC time. Do not stop reads.
2. Create and checksum the backup:

   ```bash
   mkdir -p backups
   export STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
   $DC exec -T db pg_dump -U hub -d hub > "backups/hub_${STAMP}.sql"
   test -s "backups/hub_${STAMP}.sql"
   grep -q 'PostgreSQL database dump complete' "backups/hub_${STAMP}.sql"
   sha256sum "backups/hub_${STAMP}.sql" | tee "backups/hub_${STAMP}.sql.sha256"
   ```

3. Restore into an isolated, disposable PostgreSQL 16 container with no
   production network or volume, and fail on the first SQL error:

   ```bash
   docker rm -f "hub-restore-${STAMP}" 2>/dev/null || true
   docker run -d --name "hub-restore-${STAMP}" -e POSTGRES_PASSWORD=verify \
     -e POSTGRES_USER=hub -e POSTGRES_DB=restore_verify postgres:16
   until docker exec "hub-restore-${STAMP}" pg_isready -U hub -d restore_verify; do sleep 2; done
   docker exec -i "hub-restore-${STAMP}" psql -v ON_ERROR_STOP=1 -U hub \
     -d restore_verify < "backups/hub_${STAMP}.sql"
   docker exec "hub-restore-${STAMP}" psql -At -U hub -d restore_verify \
     -c "SELECT 'posting_batches',count(*) FROM core_postingbatch UNION ALL SELECT 'posting_entries',count(*) FROM core_postingentry UNION ALL SELECT 'audit_events',count(*) FROM core_auditevent;" \
     | tee "backups/hub_${STAMP}.restore-counts.txt"
   ```

4. Compare those counts with the source counts captured at quiescence, verify
   representative oldest/newest records and totals, and run Django checks
   against the isolated restore using an explicitly isolated database URL.
   The database operator signs the restore evidence. Remove the disposable
   container only after sign-off. A restore error or count/total mismatch is a
   hard stop.

## 3. Deploy and migrate

Keep finance posting quiesced and flags off. Run the normal deploy through image
build and prechecks. Before `migrate`, inspect active locks and long-running
transactions; stop if any finance table is busy. Apply migrations once:

```bash
timeout --signal=INT --kill-after=30s 10m \
  $DC exec -T web python manage.py migrate --noinput
$DC exec -T web python manage.py showmigrations --plan
$DC exec -T web python manage.py check
$DC exec -T web python manage.py smoke_check
$DC exec -T web python manage.py system_audit
```

If a timeout, lost session, deadlock, or database error occurs, **do not blindly
rerun**. Keep flags off; inspect PostgreSQL activity and `showmigrations`, prove
whether the transaction committed or rolled back, compare schema to the reviewed
plan, and have the database operator decide whether a forward retry is safe.
Never use `--fake` without a separately reviewed incident plan. Do not reverse an
additive finance migration after it may contain production records.

Verify public and authenticated routes, logs, schema, and that the old
application can still read the database. Re-enable non-posting traffic only.

## 4. Backfill

Backfill is a separate, resumable operational step, not hidden inside schema
migration. `reconcile_finance` is read-only unless `--apply-backfill` is supplied;
the current backfill only applies deterministic projections and persists its
cursor/review state.

1. Count eligible records and choose date windows of **at most 1,000 records**.
   Begin with a 100-record canary. Shrink the date window if a batch approaches
   five minutes, causes replication lag above 30 seconds, lock waits above five
   seconds, or database CPU above 70%. Do not increase beyond 1,000 without a
   reviewed code change that provides bounded transactions.
2. Run one window at a time, oldest first (replace dates with the recorded
   inclusive window):

   ```bash
   timeout --signal=INT --kill-after=30s 10m $DC exec -T web \
     python manage.py reconcile_finance --apply-backfill \
       --date-from 2026-01-01 --date-to 2026-01-01 --format=json \
     | tee backfill-2026-01-01.json
   timeout --signal=INT --kill-after=30s 10m $DC exec -T web \
     python manage.py reconcile_finance --check \
       --date-from 2026-01-01 --date-to 2026-01-01 --format=json \
     | tee reconcile-2026-01-01.json
   ```

3. Between batches, record duration, applied count, remaining count, locks, lag,
   CPU, errors, and review items. Reconcile totals to the source snapshot.
4. On timeout or disconnect, assume the outcome is unknown. Stop, inspect the
   durable `FinanceReconciliationState` and resulting projections, run the
   read-only reconciliation for that window, and only then rerun the same window.
   Idempotency/cursors make a verified retry safe; never delete partial history.
5. Human-review findings are assigned to the finance owner. Resolve them through
   supported domain actions or leave the cutover blocked; never fuzzy-match an
   identity or mutate posted history.

After all windows, run full-history reconciliation twice: once immediately and
again after normal posting resumes under the write canary.

## 5. Reconciliation and ordered cutover

At every step, capture effective flags and both reconciliation outputs. Wait at
least 15 minutes and one representative finance operation between flag changes.
The required order is strict:

1. **Legacy only:** writes `False`, dual reads `False`, report reads `False`.
2. With finance-owner and IC approval, set only
   `POSTING_LEDGER_WRITES_ENABLED=True`; keep both read flags `False`. Recreate
   web, verify effective flags, resume a small posting canary, and verify each
   source, posting command, balanced batch/entries, legacy projection, and audit
   event. Reconcile.
3. After zero discrepancies, finance-owner and IC approval, and a 15-minute
   clean canary, set `POSTING_DUAL_READ_ENABLED=True`. Reports still use legacy
   reads. Compare every surfaced total and reconcile.
4. Keep dual reads for at least one complete business-day close. Reconcile the
   whole history and the closed date, compare account/report totals exactly,
   and obtain fresh finance-owner and IC approval.
5. Set `POSTING_REPORT_READS_ENABLED=True` last. Recreate web, verify effective
   flags, validate reports against the saved legacy totals, and begin heightened
   monitoring.

After each phase:

```bash
$DC exec -T web python manage.py shell -c \
  "from core.services.posting.rollout import current_rollout; print(current_rollout())"
$DC exec -T web python manage.py reconcile_postings
$DC exec -T web python manage.py reconcile_finance --check --format=json
```

Do not enable a database flag while an environment override pins it off. Do not
enable report reads before dual reads, or dual reads before writes.

## 6. Monitoring and ownership

The deployment operator watches continuously for the first hour; the IC owns
alerts for 24 hours and through the first business-day close; the finance owner
signs off that close. Compare five-minute windows with the saved baseline:

- request volume, HTTP 5xx, latency, worker restarts, and exceptions;
- posting throughput/latency/errors, idempotency conflicts, unbalanced batches,
  missing commands/audits, and unresolved reconciliation failures;
- legacy versus ledger totals by account, payment method, business date, close,
  transfers, reversals, purchases, cash, and stock;
- PostgreSQL locks, slow queries, CPU, storage, connections, replication lag,
  and backup health.

Run `reconcile_postings` and full `reconcile_finance --check --format=json` after
each flag change, every 15 minutes for the first hour, hourly for the next 23
hours, and after the first close. Any fixed-threshold breach triggers rollback;
retain output and page the IC and finance owner.

## 7. Rollback

Rollback is forward-compatible and preserves history.

1. Declare the incident, record UTC time/last request key, quiesce finance
   operations, and stop backfill/workers. Do not delete or edit posting/audit
   rows.
2. Set **all three environment overrides to `False`**, then recreate web. This
   first disables new ledger posting and forces compatible legacy reads even if
   database flags remain enabled:

   ```bash
   $DC up -d --no-deps --force-recreate web
   $DC exec -T web python manage.py shell -c \
     "from core.services.posting.rollout import current_rollout; print(current_rollout())"
   ```

   Expected: `ledger_writes=False, dual_reads=False, report_reads=False`.
3. If needed, deploy the recorded previous **schema-compatible** image/commit.
   Do not run destructive reverse migrations and do not restore the pre-deploy
   database merely to roll back application code: doing so would erase valid
   postings/audits created after the backup.
4. Smoke-test legacy reads and critical routes; run both reconciliation commands
   read-only and preserve their output. Keep posting closed until the finance
   owner confirms legacy report continuity and the IC confirms stability.
5. Correct valid financial effects only through compensating/reversal postings
   after review. Leave new ledger and audit records in place for investigation.

A database restore is disaster recovery, not normal rollback. If physical
corruption makes restore unavoidable, the database operator and IC must create a
separate recovery plan that preserves/export-posts all post-backup posting and
audit records, obtains finance-owner approval, restores into isolation first,
and proves no committed financial history will be silently lost.

## 8. Close the change

Attach the migration plan/SQL, test results, backup checksum and restore counts,
flag transitions, batch manifests, reconciliation JSON, monitoring graphs,
approvals, and rollback decision. The finance owner and IC close the change only
after the first complete close reconciles. Keep legacy fields and compatibility
reads until the separately approved retention-period release described above.
