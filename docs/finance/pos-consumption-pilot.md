# Phase 1: categorized POS hospitality and owner personal consumption

Operational behavior only. **Never** pretend a free POS item was paid in cash.

- Existing order items continue through the same kitchen and recipe-stock
  deduction path as paid items (only if automatic deduction is enabled).
  This feature does **not** directly adjust inventory or post D10 accounting.
- Manager-authorized **ضيافة هَب** and superuser-only
  **استخدام شخصي للمالك** are the initial two categories.
- Choose an order item and an integer quantity (including part of a
  multi-quantity line). Unit selling price is derived from its **historical
  order line snapshot**; related `OrderDiscount.COMP` reduces the customer's
  amount due while `OrderConsumption` records why, for whom (optional brief
  note), approver, quantity, selling value and **estimated** cost when known.
- Do not accept items such as Internet services or non-discountable products.
  No use after any payment or closed/cancelled visit, and no use on orders
  with other unclassified discounts (including membership) in this pilot.
- Each submitted form includes a UUID request key; duplicate requests cannot
  double-discount. AuditEvent and ActivityLog preserve attribution.
- Orders with classified consumption must not be modified by legacy
  order-item editors. Add new items as a separate order to the visit.
- The owner's report is an **informational statement**, not a receivable,
  owner withdrawal accounting journal, payroll benefit or paid invoice.
  Choice of future owner draw/reimbursement is an intent only.
- Existing generic discounts remain otherwise available, but the old
  unclassified 'comp' action is disabled for **new** legacy discounts.
  Historic discount records remain untouched.

## Deferred until explicitly agreed
Direct inventory withdrawal; staff allowances; event accounting; money
collections against an owner account; accounting/COGS recognition under D10;
financial reversal/returns of complimentary items; zero-balance combined
visit closure integration. Never infer this approval from POS classification.
