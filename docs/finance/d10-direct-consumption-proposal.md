# D10 proposal — direct internal consumption and hospitality (NOT APPROVED)

**Status: PROPOSED / ALL STOCK AND FINANCIAL POSTING STILL BLOCKED.**
This document requests a focused owner/finance-lead decision; it does not
amend the authority table in `docs/finance/domain-decisions.md`.

## Operating problem

Hub staff and the owner may take stock directly without a retail POS order:
bottled drinks, kitchen ingredients, snacks, catering for visiting artists, and
staff food or coffee. The existing classified POS workflow covers items
recorded as order lines; it must not generate a second stock movement.

The existing manual stock movement form is intentionally disabled because D10
has no approved expense/COGS/variance recognition, authorization, or reversal
policy. Re-enabling the old handler would be unsafe.

## Proposed categories (independently selectable)

1. `owner_personal`: personal/family consumption; record against a **distinct
   proposed owner-draw or owner-reimbursement treatment**, not generic
   hospitality. A reimbursement intention is not an actual receivable/payment.
2. `hub_hospitality`: genuine Hub business hosting of visitors/partners.
3. `staff_benefit`: explicitly permitted staff meal/drink during a shift.
4. `event_promotion`: event/artist/promotional samples; optional event ID.

Waste, spoilage, recipes, stock count corrections, purchase returns and
actual sales are **not** interchangeable with these four categories.

## Six D10 decisions for owner/finance lead (all require explicit approval)

### D10-1. Account mapping and valuation — PROPOSED
- **Quantity**: inventory item's canonical base unit; accept compatible
  gram/kilogram and millilitre/litre conversions from
  `core.services.inventory_unit_entry`; no box/bottle/pack guesses.
- **Value**: snapshot a unit valuation from an approved costing source at
  posting time. Missing cost must block monetary posting rather than become
  zero. Do not silently treat estimated sales price as consumption cost.
- Proposed mapping for approval (codes must be created and verified before use):
  - Hospitality: Dr `expense:hub_hospitality`, Cr inventory asset
  - Staff: Dr `expense:staff_benefits`, Cr inventory asset
  - Events: Dr `expense:event_promotion`, Cr inventory asset
  - Owner personal: Dr `equity:owner_draw` **or** Dr an explicitly approved
    owner receivable only if reimbursement is properly established; Cr inventory
    asset. The choice is owner-specific, not a universal operating expense.
- These accounts do **not** exist by assumption. Actual inventory asset
  account mapping and valuation source need finance review; do not hardcode
  `inventory:purchases` without checking the current inventory lifecycle.

### D10-2. Recorder, approver, thresholds — PROPOSED
- Permitted staff inventory users can **draft a request**, not post.
- Admin/operations manager approves business hospitality/staff/event
  requests; staff may not approve their own consumption.
- Owner personal consumption requires owner/superuser confirmation and is
  reported separately; owner may self-authorize if this exception is approved.
- Large requests need second approval above a configurable threshold; the
  numeric threshold is **TBD**. Never infer it from supplier-payment rules.

### D10-3. Recognition moment — PROPOSED
- A draft or approval request does not change stock.
- After explicit D10 release approval, a validated `post_consumption`
  command would atomically deduct stock and post the balanced accounting
  entry only when physical handover/use is confirmed.
- A POS order already deducted from stock may only be classified, never
  deducted again.

### D10-4. Date and period close — PROPOSED
- Use a user-visible `business_date` (Damascus business calendar).
- Check that corresponding account/day periods are open before posting;
  no retroactive ledger posting into a closed date.
- Created/approved timestamps remain audit times, not ledger business date.

### D10-5. Reversal/cancellation — PROPOSED
- Unposted draft requests may be cancelled with a reason.
- Posted records are immutable. Correct through an atomic, linked inverse
  stock and accounting entry on an **open** date, with distinct authorization.
- Physical restocking must be confirmed; no implied return of consumed food
  or automatic reverse movement for items that cannot actually return.
- Refuse negative stock and concurrent over-consumption.

### D10-6. Reporting/reconciliation — PROPOSED
- Monthly breakdown per category, actor, approver, inventory item, quantity
  in canonical unit, unit cost snapshot, total cost, business date and
  associated event/owner treatment.
- Show estimated sale-price equivalent **as contextual information only**.
  Do not add it to revenue or mix it with actual cashier collections.
- POS-classified consumption and direct withdrawal records must have
  non-overlapping identifiers and reconcile with recipe deductions.
- Owner statement distinguishes proposed reimbursement, posted owner draws,
  and **actual** money received if/when owner settlement is separately
  authorized. Never automatically net them.

## Proposed implementation batches (after D10 decision)

**Batch 1: non-posting intake.** A small, explicitly labelled *pending
request* form stores item, category, requested original/base quantity, reason,
business date, actor, optional proposed beneficiary/event, cost estimate when
known and idempotent request key. Review list shows **NOT deducted**.
No stock movement, accounting entry, inventory adjustment or payment.

**Batch 2: authorized atomic posting.** After six D10 decisions are
approved, add a single service with row locks, account validation,
approval-role checks, period-close checks, no-negative-stock protection,
idempotency, a posted source link and financial/stock snapshots. The
existing manual movement form remains blocked until proven safe.

**Batch 3: reversals, reporting, and pilot.** Linked inverse postings,
cashier-vs-direct reconciliation, missing-cost checks, staff policy limits,
360px RTL and low-connectivity acceptance, PostgreSQL concurrency tests.

## Approval questions (must be answered before Batch 2)

1. For owner personal use: treat as **equity draw by default**, or create
   genuine reimbursable owner receivable? Can these vary per transaction?
2. Identify actual inventory asset account(s), cost valuation method, and
   expense/equity/receivable account codes for each category.
3. Which roles approve staff and visitor hospitality; is owner self-approval
   acceptable for personal use; what limits trigger second-person approval?
4. Is physical handover confirmation the recognition event?
5. Who can reverse postings, and can an item ever be returned to inventory?
6. Confirm reporting, cutoffs, and exclusion of duplicated POS recipe use.

**No policies are considered approved merely because this document exists.**

## Update 2026-10-19

The stock-only subset was explicitly approved by the owner. See the authoritative
`D10 limited stock-only exception approved 2026-10-19` section in
`domain-decisions.md`. The atomic stock-plus-ledger proposal above remains
unapproved; implementation must not activate its accounting mappings.
