# Hub Suite 3.1 — Phase 4a: safe order/account corrections

Status: feature branch, not deployed. This is **the first bounded Phase 4 batch**, not all potential refunds or bill splitting.

## Operator experience

On the existing staff visit drawer:
- Edit the session note in place. The server records old/new values and actor in ActivityLog, only on actual changes.
- See each order with an optional **نقل الطلب إلى حساب آخر** expander for authorized staff. Choose an existing open destination visit, enter a reason, confirm. A successful transfer returns the refreshed drawer with no extra specialist page; native POST remains available if HTMX is absent.
- Whole-order transfer changes the visit assignment and its table context. It updates both visits' activity timestamps. It does **not** move partial item lines or any payment records.
- Existing attach/detach endpoints are routed through the same guard to prevent bypassing safeguards through their older full-page forms.

Transfer is available only to accounts with both **visit_manage** and **cashier** capabilities. The current checkout/payment flow, receipt, QR and menu/session behavior are untouched.

## Financial and service safeguards

All in one database transaction:
1. Lock the involved HubVisit records in ascending PK order and require both open.
2. Lock the order, validate source membership (stale tabs cannot transfer again).
3. Reject cancelled, delivery/takeaway, or actively preparing/ready-to-serve orders.
4. Reject **any** Payment history, including reversed/inactive payments, not just unpaid balances. Paid/refunded posting has to be corrected by an approved ledger reversal process.
5. Reject linked InternetEntitlement/InternetSession, discounts, and incompatible membership association.
6. Transfer the whole order and table context, audit reason, actor, old/new visit and table. No invented payment/refund postings, no erasure of original records.

This is for routine mistaken account/table assignment or moving an eligible unpaid whole order to another account. It is **not** a general-purpose paid-bill transfer.

## Not implemented in this batch

- Splitting one order into selected item lines (requires quantity splitting, inventory, exact historical price/options and preparation state preservation).
- Automatically creating a new browser-bound customer visit from an existing visit (requires explicit customer privacy and account-access decisions).
- Merging paid visits or moving historical allocations between settled accounts.
- Posting refund/payment reversals from the staff drawer (D03 policy demands a separate linked opposing posting, actor/approver and reason, and verification against close-day locks).
- Any change to MikroTik, internet sessions, inventory, POS pricing or preparation modes.

## Release checkpoints

Run one full Django CI on the PR (including PostgreSQL, migration check, static collection). Before production use, a brief staff smoke should cover: open visit drawer on phone, edit notes, move a new unpaid order to another open visit, verify both balances, attempt blocked paid move, check waiter role, and confirm all older paths still work.

Future Phase 4b should design/implement order-line splitting and financial reversal separately, reusing the existing approved posting service. Phase 5 is information architecture and mobile/accessibility consolidation.
