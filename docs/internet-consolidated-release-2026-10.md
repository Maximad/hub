# Hub Suite — Consolidated Internet Release (October 2026)

## Scope and release decision

Deploy merged PRs #269 through #275 together, plus the release-readiness
checks in this change. The scope is Django application code and database
migrations only. Do **not** modify MikroTik DNS, firewall, HotSpot server,
speed profiles, authentication profile or TLS settings.

The deployment does **not** enable automatic staff device caps or debit
daily/total staff minute balances. RouterOS read-only observations and sampled
device-minutes are insufficient for authoritative enforcement.

Do not claim the deployment is complete until the VPS deployed-revision file,
app and worker processes, public HTTPS, and real phone testing are verified.

## Before the one production cutover (read-only)

1. Check GitHub main has a successful full PostgreSQL CI run, no open
   conflicting PR and the intended release commit.
2. On the VPS as user deploy, inspect the current revision and local changes,
   the presence of BOTH docker-compose.prod.yml and the locally maintained
   docker-compose.mikrotik.yml, the protected .env and the CA bind source.
   Never print or share .env, router credentials or raw MAC addresses.
3. Confirm MikroTik TLS verification is true and the effective Compose CA
   mount exists, is readable, and is read-only for the web, internet-worker
   and management-mcp services. These are operational Compose requirements,
   **not** RouterOS configuration changes. Stop and resolve locally if any
   assertion fails.
4. Record only aggregate counts: staff grants, statuses, network operation
   backlog and unknown-state warnings. Previously observed: six active staff
   grants, but do not hard-code six as today's expectation.
5. Confirm normal customer traffic can tolerate a brief web restart.
   Schedule a quiet period; tell active cashiers not to finalize an Internet
   purchase during the cutover.
6. Do not start production rollout with failed backups, an unverified CA
   mount, pending unreviewed application changes or failed CI.

## Release execution

The existing scripts/deploy-production.sh, run as deploy from /opt/hub on
main, is authoritative and already:

- uses the **combined** Compose files if the local overlay exists and refuses
  deployment without the overlay when MikroTik is enabled;
- takes a consistent database/media backup, verifies its checksums and
  restores it into an isolated PostgreSQL container before source upgrade;
- builds target images, checks migrations, migrates the DB, then runs launch
  readiness and the new Internet release audit before container replacement;
- recreates web, management-mcp, internet-worker and notification-worker with
  both overlays, performs smoke/readiness checks and updates the deployed
  revision marker only after completion.

For the first installation of *new script-level* preflight changes, remember
that the script already loaded by Bash may be the old copy until the source
fast-forward occurs. Independently verify effective CA mounts and TLS before
invoking the currently installed script. Do not skip this because target CI
passed. Avoid printing the rendered Compose configuration, which may contain
secrets. If the local overlay does not supply the CA read-only to all required
Django services, stop and fix the local Compose override **without touching
RouterOS** before proceeding.

Typical invocation (after independent preflight):

    cd /opt/hub
    ALLOW_PRELAUNCH_OPERATIONAL_DATA=true ./scripts/deploy-production.sh

The operational data flag retains real visits, staff grants, orders and
payments; it does not clear or reset them.

## Post-deployment verification

Use BOTH Compose files and the private .env:

    docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env ps
    docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env exec -T web python manage.py internet_release_audit --json
    docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env exec -T web python manage.py mikrotik_healthcheck --json
    docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env exec -T web python manage.py internet_readiness --json
    cat backups/last_deployed_revision.txt

Check no critical ERROR/FATAL patterns in Django and Internet worker logs.
The audit displays only aggregate counts. Compare grant counts with the
pre-cutover record, confirm legitimate active staff grants remain active, and
review any disabled-staff revocations and pending network operations.

## Real-device acceptance matrix (record PASS / FAIL / NOT TESTED)

A. Customer phone, no table: browse menu unaffected, choose fast Wi-Fi,
   enter name + phone, confirm connection path; invalid/blank identity must
   not create access. Verify charge belongs to that independent visit.
B. Two customers, same table: customer 1 opens a visit; customer 2 creates
   a separate visit and must see a separate bill. Joining the existing bill
   needs the actual PIN; table number or QR alone cannot attach the charge.
C. Pricing/billing: metered and package paths show appropriate rate/billing
   details, only network-activated sessions are chargeable, no duplicate
   Internet orders after double POST/reconnect. Closing visit must retain
   the correct linked Internet item.
D. Basic Wi-Fi: connecting without buying fast Internet still works;
   upgrading and returning to basic respects remaining basic allowance.
E. Staff phone: log in as active staff; use existing grant even if customer
   self-service is disabled; observe online count, then disconnect and
   verify it drops on a later worker poll. Do not infer success from relays.
F. Another staff device: compare distinct device count and shared login
   behavior; test MAC randomization and rapid reconnect. Observed-address
   history is not validated physical-device identity.
G. Deactivated staff (designated test user only): disable through Django,
   verify grant cancellation/disconnect operation and no automatic
   reactivation. Never experiment on a production employee without
   approval.
H. Provider isolation: staff complimentary access creates no customer
   order, payment, revenue share or provider-visible staff customer record.
I. Reliability: confirm TLS-verified MikroTik health, CA mounts, worker
   heartbeat, fresh connection snapshots and stale/unknown display on a
   disconnected/unavailable read. No router config changes.

**No automatic quota enforcement** until designated-phone results,
outage/reconnect handling and a separate enforcement policy are reviewed.

## Failure policy

Before cutover: stop. Preserve backups, old running containers and source
revision. Do not silence launch_readiness or Internet release audit FAILs.

After cutover: preserve logs and the backup path; avoid an unreviewed data
restore or destructive migration rollback. The migration sequence is additive
but restoring previous code and previously migrated DB still needs a
compatibility check. Revert only through the established safe deployment/
rollback procedure, not ad-hoc router changes.

If the only blocker is failed CA-mount verification, fix only the local Docker
override or missing certificate file, not RouterOS settings or TLS verification.
