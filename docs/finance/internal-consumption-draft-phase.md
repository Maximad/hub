# Internal consumption requests — Phase 2 (D10 remains blocked)

This is **not** an inventory deduction engine. Staff can enter
evidence-only **requests** for Hub hospitality, owner personal use,
staff consumption, or event/promotional use. It explicitly does **not**
mean the items have been physically issued or deducted.

- Inventory → «طلبات الاستهلاك (دون خصم)» exposes a simple entry form
  with g/kg and ml/litre conversion and exact canonical base units.
- Owner personal category is superuser-only. An authorized manager can
  mark a request reviewed or cancelled, but **review is not approval to post**.
- A request stores actor, category, brief reason, entered quantity,
  converted quantity, business date, unit-cost estimate snapshot (not
  recognized expense), review note and UUID idempotency key.
- The request deliberately does not create or change `StockMovement`,
  `InventoryItem.current_quantity`, `PostingBatch`, `Expense`,
  `Payment`, `CashMovement` or an owner payable.
- Legacy manual stock entry remains blocked. D10 must be formally
  confirmed, including account mapping, permitted approvers and
  thresholds, timing, date/close, reversal, negative-stock and
  valuation/recipe reconciliation, before any subsequent posting
  service can be introduced.
- Existing POS order-item `OrderConsumption` is different: do not
  draft an additional inventory request for the *same* already-deducted
  recipe usage. Future release should link to originating POS/event
  records to avoid double-counting.

This pilot has no release approval or production deployment.
Run full PostgreSQL CI and verify Arabic mobile form before merging.
