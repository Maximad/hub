# Hub Suite P0 — Manual inventory movement gate (D10)

Date: 2026-10-14
Scope: staff Inventory → movements, waste and manual corrections.
Production status: **not deployed** at the time of this change. No stock migration.

## Verified defect

The legacy `staff_inventory_movement_new` POST handler attempted to call
`core.services.posting.purchases.adjust_stock`. The referenced service is
absent. The previous form displayed manual movement types and directions
without checking that accounting and stock recognition policy was approved.

## Policy boundary

`docs/finance/domain-decisions.md`, D10 ("Inventory adjustment, waste, or
production consumption") remains **UNCONFIRMED / POSTING BLOCKED**.

Do not invent expense/waste/COGS/variance ledger accounts, approvals, automatic
recognition dates, negative-stock policy or reversing entries. A missing
function is not permission to bypass a blocked finance decision.

## Bounded launch fix

- Historical stock movements, the low-stock view and inventory reports remain
  readable.
- The old manual-entry URL is retained for bookmarks, but instead of offering
  a form that crashes on POST, it gives an explicit "not yet available"
  explanation and links to permitted alternative workflows.
- All POST attempts to the old URL are rejected with a clear message. No
  `StockMovement`, `PostingBatch`, cash movement, audit event or quantity
  change is made. Invalid/malformed inputs cannot raise the old missing-method
  error.
- Navigation no longer advertises a working waste-entry button.
- Inventory **physical count** remains a distinct restricted operational
  correction action at `staff_inventory_stock_count`. Only full-admin,
  "تشغيل وكاشير", or individually granted `inventory_stock_count` users
  may perform it. It does not create a financial valuation journal.
- Existing **purchase receipt** flow remains separate and must be authorized
  via the approved D07 rules; saving a purchase draft alone does not add stock.

## What must be decided before implementing a proper waste workflow

Record and approve the six dimensions still listed as TBD in D10:
account mapping/valuation, authorized recorder/approver and tolerances,
recognition moment, business date and period close, reversal/cancellation,
and quantity/value reconciliation with recipe and production records.

Then implement a bounded, service-backed, atomic and idempotent workflow with
clear direction defaults, role-specific options, required reasons, stock
availability checks, immutable audit records and tests for duplicate submits,
concurrency and retry. Restore the manual form only after D10 is formally
approved and automated + phone acceptance checks pass.

## Acceptance and deployment notes

Current branch CI must pass the full Django/PostgreSQL suite. Merge only
after green checks, do not deploy until the planned batched release.
The VPS, `.env`, existing inventory values, finance flags and MikroTik
configuration must remain untouched. During a later pilot, recheck this
screen after deployment and confirm no duplicate or hidden stock mutation.
