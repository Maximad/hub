# Hub launch — customer payment consistency diagnostic

Status: development-only Batch A, read-only. This is **not** a production deployment,
data repair, finance policy approval, Internet quota mechanism, or automatic close gate.

## Purpose

Investigate a cash payment that appears settled in one Hub staff/customer screen
but not another. Do not add another payment or alter old records to force a
visual match. First compare:

1. The order's original amount and active, unreversed collections.
2. The whole customer visit (all non-cancelled orders), including partial
   allocations across multiple orders.
3. Each collection's matching cash movement and two-entry posted journal.
4. Any linked Internet session's **billing** state separately from the linked
   order's **payment** state.
5. Posting business dates when investigating differences between today's
   cash figures and the order's historical sales date.

The diagnostic uses the actual Django financial models and the same visit
aggregation function as the consolidated cashier. It has no database writes,
model saves, RouterOS calls, network workers or automatic cleanup.

## Commands

Run on an authorized Django environment **only after** the release is
appropriately deployed. For example, from the Hub deployment directory:

```bash
docker compose exec web python manage.py audit_customer_payments --recent 10 --json
docker compose exec web python manage.py audit_customer_payments --payment-id 123 --json
docker compose exec web python manage.py audit_customer_payments --visit-code <visit-public-code> --json
```

Use the actual Compose invocation and override files for the environment; do
not bypass the deployment's Internet TLS/CA preflight. A production host can
have multiple Compose overlays.

Options are mutually exclusive. `--recent` accepts 1–30 collections; start
with 10 and locate the payment ID or visit code. Do not publish the output
because it contains operational financial amounts and identifiers, although
it deliberately omits names, phone numbers, IP addresses, MACs and secrets.

A `PASS` means **only** that the records selected by this command have no
detected anomalies. It does not prove that browser state is fresh, that the
daily financial report is correct, or that all system records reconcile.
A `WARN` identifies exceptions for manual inspection. Neither status writes
a correction or blocks deployments. A historical/legacy payment may have
no journal; investigate it rather than fabricating a new collection.

For the reported incident, collect the corresponding order/visit identifiers,
the actual command output, the amounts shown by the cashier, Operations,
customer view and finance report, the deployed git revision, and approximate
business date/time. The command alone cannot inspect live browser caching,
screen refreshes, or deployment mismatch.

## Boundaries

- Cash is the operational method. Manual-transfer records are visible for
  legacy/future compatibility; no electronic payment integration is activated.
- `InternetSession.status='billed'` means its Internet charge was materialized,
  **not** necessarily that the corresponding Order was paid. The diagnostic
  reports the linked order's actual remaining balance independently.
- Unbilled active Internet sessions may legitimately have no linked order
  yet. They are shown as `not_billed`, not automatically converted into debts.
- Internal staff Internet grants and observed device continuity are never
  treated as customer payments, sold time, quota enforcement or revenue.
- The new `internet_release_audit` and unchanged-router phone acceptance
  checklist from PR #276 remain required for the consolidated deployment.
- Inventory, recipes and cost data are deliberately outside this payment
  inspection. Missing inventory must not lead to financial backfills.

## Next batch

Once the actual mismatch is classified, make the smallest verified
presentation or posting change and test: one visit, several orders,
partial/complete cash, idempotent repeat, payment reversal, Internet
finalization/settlement, old/new business-date boundaries, and stale
contextual drawer refresh. Keep existing recorded cash and Internet
histories immutable unless an explicitly authorized correction workflow
is used.
