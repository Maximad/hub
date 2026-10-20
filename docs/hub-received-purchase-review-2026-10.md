# Hub Sweida: purchase-receipt follow-up, not a historical reconciliation

## Owner direction

Make staff selling, buying, stock counting and Internet easy and authoritative **now**. Many old rows were reconstructed from paper and must never trigger automated operational claims merely because they are dated recently. Costs and finance closeout are reviewed progressively.

## Existing architecture reused

PR #326 already introduced `OperationalReviewCase` and its daily `BusinessDayTask` projection. Subsequent releases already added verified-only low-stock review and durable Internet provision-failure follow-up. **No second task engine is built here.**

This patch introduces one optional new case per **Purchase** with an actual non-reversed **PurchaseReceipt** and at least one receipt line:

- `source_type=purchase`
- `issue_code=receipt_review`
- source label is deliberately generic `شراء مستلم #<id>`.
- one stable review case persists across Business Days; no task duplication.
- case is opened if its supplier is unidentified, a current posted receipt movement has a zero or missing unit cost, or an unreversed receipt line lacks its linked stock movement.

**Important:** Changing the editable PurchaseItem unit price later does not resolve a zero/missing cost from a *posted* receipt movement. Staff should not edit old posted prices to make a warning disappear. Actual financial corrections require the existing authorized receipt reversal/reposting policy or a later audited correction flow. An explicitly free donated item may also need proper classification, not a fictional positive cost.

Notebook-imported Purchase records linked through `OperationsImportReceipt` are ineligible for this workflow, even if manually selected. Purchase candidates are **never selected automatically** merely from recent creation dates or recent receipt movement dates. An authorized manager can select a known purchase ID explicitly; previously tracked cases are subsequently re-evaluated.

The current duty holder must be checked in and must have the Inventory capability to see the task outside manager view. The existing task page links to the capability-gated purchase detail. Without authorized active coverage, it remains on the manager review queue. It does not set a due time or send reminder broadcasts.

## Changes permitted by this PR

Only schema **choices** (source/issue enumeration), opt-in review-case rows, daily task projections and audit events after explicit approval. It never mutates purchases, receipts, original unit prices, posted ledger batches, stock quantities, supplier payments, Internet sessions or MikroTik settings.

There is no data backfill and no automatic job or signal.

## If/when staff operations are approved for a pilot

This is NOT required immediately. After full CI, merge and safe deploy, the manager may use the existing preview-first command with the actual known purchase ID:

    python manage.py review_operational_followups --no-recent --purchase-id <verified_purchase_id>

It rolls back all writes. Only after reviewing the proposed case:

    python manage.py review_operational_followups --no-recent --purchase-id <verified_purchase_id> --apply --actor <manager_username>

The staff interface is the existing `/staff/operations/tasks/`; the linked review action opens the actual purchase. No source mutation is performed by the task. No purchase financial conclusion is certified by this check.

## Deferred

- Resolving confirmed posted valuation errors through explicit financial correction policies.
- Supplier performance rankings, average costs, automated replenishment and demand predictions.
- Import reconciliation of notebook rows.
- Scheduled background review sync or push-notification spam.
- Staff-device acceptance, to be scheduled by the owner later.
