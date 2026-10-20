# Hub Sweida — verified-only low stock (2026-10)

## Why this matters

Hub's historical amounts were partially reconstructed from notebooks and entered incrementally. In particular, a recorded zero is **not** evidence that a physical ingredient is out of stock. Before this change, `sync_business_day_tasks` and staff inventory pages could issue low-stock warnings based solely on those uncertain stored figures.

## One shared operational gate

The read-only `verified_low_stock_items()` helper in `core/services/operational_completeness.py` returns only currently active items where all conditions hold:

1. A physical stock count was explicitly confirmed in the existing authorized stock-count workflow (recorded as `inventory_stock_count_confirmed`, even if there was no adjustment).
2. The current quantity is at or below a configured `low_stock_threshold`.
3. That threshold is present.

The helper powers:
- the business-day aggregated `inventory:low-stock-review` task
- the staff inventory dashboard low-stock list
- the staff low-stock review page

An uncounted zero no longer creates a low-stock task. When the first actual count is confirmed, later Business Day synchronization can create the existing aggregated review task; when the threshold breach is no longer present, the existing task reconciliation can auto-waive the pending notification. No new task system, schema or timer was created. The existing grouped task has no `due_at` deadline: it remains a visible human review, not an overdue notification broadcast.

## Limits and safety

- This is a **human stock review**, not an instruction to order, a verified cost, or a recommendation of purchase quantity.
- A historical balance is not certified by adding a unit cost, a supplier invoice, an imported order, or a threshold alone.
- The current gate confirms that a physical count occurred but does not certify historical purchase provenance or financial valuation.
- A later change to the stock quantity must use the approved recording path. Future work may add stronger per-movement reconciliation, before allowing automated projections.
- Automatic inventory deduction, purchasing, supplier payment and RouterOS/MikroTik remain untouched.
- The separate `OperationalReviewCase` implementation is the canonical source-linked follow-up engine; this PR only fixes the legacy aggregated task's false positive warning, rather than adding any competing per-item tasks.
- Production stays unchanged until the normal CI, merge, verified backup/deployment and operator approval process.

## Tests included

- Historical zero never generates a low-stock task.
- Confirmed zero generates a **review-only** daily task.
- A previously unverified low balance does not appear on dashboard/low-stock page; confirmed count does.
- Quantity above threshold clears the warning; a removed threshold clears it.
- Existing generated-task auto-waive works when the material's stock is no longer low.
- No unwanted stock movement, payment or purchase occurs during counting or warning evaluation.

## Later work

Once real receiving and counting have established trusted stock histories, implement the next bounded operational features through the existing `OperationalReviewCase` and current-staff authorization:

1. Source-backed procurement review (verified receipt, missing vendor/unit cost).
2. Internet session issue follow-up from actual failed session state, no router changes or credentials in notices.
3. Verifiable provenance of later stock movements and conservative purchase planning.

No forecast trained on notebook imports.
