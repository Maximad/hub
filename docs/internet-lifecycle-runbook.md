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

## Membership scheduled activation and recovery

The same lifecycle reconciliation command now checks **due, previously sold**
memberships. It selects only `pending` subscriptions with a sale idempotency
key, whose start is due and whose end has not passed; unattended retries skip
records already carrying an activation error. The `--dry-run --json` result
includes `subscriptions_to_activate` (IDs only).

Before activation the service rechecks the original order and total, member
status, complimentary-sale provenance or full active cash/transfer payment.
It creates **no new sale or payment**. Activation (including derived Internet
entitlements and allocations) runs atomically and remains idempotent under
concurrent workers. A failure keeps the existing sale `pending`, records
`activation_error`, and requires manager review.

An owner/admin with member management authority can open the member detail
and use **التحقق وإعادة التفعيل** on an eligible pending subscription.
This action is POST-only and CSRF-protected; it does not bypass failed payment
or expired-period checks. A pending legacy/manual grant without sale
provenance is never activated by this path.

For a standard renewal, the staff member uses the **تجديد عبر بيع جديد**
shortcut on the existing subscription. The subscription purchase form is
preselected with the original plan and the previous term's end time when
available. Completing it creates a new auditable paid sale at the current
plan price; it does not extend or rewrite an old financial record. Future
sales activate when the lifecycle worker reaches their start time.

These changes do not configure or modify MikroTik, schedule new host jobs,
backfill historic subscriptions, or change partner sharing. Check the
production runbook to verify the existing lifecycle worker is running.
