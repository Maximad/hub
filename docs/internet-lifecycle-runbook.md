# Internet lifecycle reconciliation

Run `python manage.py reconcile_internet_lifecycle --limit 200` every 1–5 minutes.
Before first deployment, run `python manage.py reconcile_internet_lifecycle --dry-run
--json` and inspect every historical candidate; do not apply an unexpected batch.

The command uses row-locked, idempotent lifecycle services. True expiry and
authorization boundaries are used even when a run is late. Re-running does not
extend a thaw twice, settle a session twice, or create another logical network job.
It only writes durable outbox operations; router availability cannot roll back the
commercial state. Failed operations remain retryable with
`python manage.py process_internet_network_operations --limit 100`.

Deployment order is: backup, deploy, migrate, `check`, targeted tests, lifecycle
dry-run, review, then a controlled Manual-backend scenario. Do not install host
cron from the application deployment and keep `MIKROTIK_ENABLED=false` until a
separate production readiness exercise.

Cancellation during a freeze does not extend the term: cancellation wins and the
old automatic thaw marker is cleared. Partial payment reversal is not supported by
the current finance API; this lifecycle integration therefore handles full reversal.
