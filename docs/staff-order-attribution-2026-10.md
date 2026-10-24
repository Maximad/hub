# Hub Suite — Sprint 5: staff order attribution (25 October 2026)

Status: implementation PR under verification, not deployed.

## Purpose

Answer four distinct questions without changing a single payment or pricing rule:

1. **Who actually entered the order?** `Order.entered_by` stores the authenticated employee on Staff POS; public-menu orders have no staff actor. Automated order creators mark `entry_source=system` and capture a human actor only when one actually initiated the operation.
2. **Where did the order originate?** `entry_source`: unknown legacy, staff POS, public menu or system-generated. Existing orders without direct evidence remain **unknown**, not falsely attributed to a nearby payment collector or shift worker.
3. **For whom?** Descriptive `recipient_kind`: customer (default), neighbor or staff, plus optional `recipient_note`. This captures context at POS; authorized cashier/manager can correct it with `order.recipient_metadata_changed` audit. Creation channel and origin actor are immutable in this correction flow.
4. **What financial treatment applies?** Only existing `OrderDiscount`, `Payment` and item-specific `OrderConsumption` records are authoritative. The new order details show actual **ضيافة هَب** or **استخدام شخصي للمالك** entries, quantity, retail reference and reason. Mixed orders can contain more than one treatment: a recipient label cannot replace these entries.

**Important:** choosing neighbor or staff never adds a discount, records hospitality, creates a benefit, moves inventory or pays the order. No neighbor membership tier/rate or staff allowance has been approved. The future policy of staff benefits or neighbor discount is **not** part of this change. Existing manager-only hospitality and owner-only personal consumption safeguards remain unchanged.

## Implementation boundaries

- Additive nullable actor + defaulted source/recipient fields and migration `core.0062`.
- Capture origin in staff POS v2 and legacy, public menu, known system-created membership, Internet and split orders.
- Show recorder/recipient on the live Operations order cards and full provenance plus classified consumption in staff order drawer/edit details.
- Recipient correction is cashier-capability and CSRF protected, atomic and ActivityLog audited. It can be performed even after a payment, but does not rewrite the monetary snapshot, previous payments, order item, creator or source.
- Keep historic records unchanged. If earlier legacy actor reconstruction is desired, do it separately using individually verifiable order-public-code audit evidence and a reviewed, dry-run-able backfill, never from a guessed shift.

## Validation

```
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core.tests.test_order_recipient_attribution core.tests.test_staff_operations_attention core.tests.test_pos_consumption
```

Run complete PostgreSQL CI before merge. Manually check Arabic RTL POS at 320/390px, staff roles, creating an order for a neighbor and staff at regular price, a partly-paid correction, read-only past orders and actual hospitality/owner records. No change to MikroTik or deployed production until a separate release verification.
