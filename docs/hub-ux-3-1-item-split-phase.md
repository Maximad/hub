# Hub Suite 3.1 — Phase 4b: unposted item-level bill splitting

Status: feature branch. No production deployment.

## Supported operation

From the **existing staff account drawer**, an operator with all three
capabilities (`visit_manage`, `cashier`, `order_edit`) may pick an eligible
order item, a positive quantity, an existing **open destination visit**, and a
required reason. The service creates **one new unpaid order** in that visit
containing the requested item quantity. This is bill splitting by item, not
a financial journal transfer and not a new browser credential.

For a partial quantity (for example three drinks, split one), the original
line is reduced from three to two and a new line in the destination order
retains original price, options, notes, prep station and cost snapshots.
When an entire line moves but other lines remain, the existing item ID is
reparented rather than duplicated. The total of both visits must remain
unchanged. The new order starts with status NEW. The original order's
general notes are not copied because they might mention a stale table;
the new order records its source order in its notes and in ActivityLog.

## Guardrails — intentionally restrictive

- Source and destination must be **different open visits**; both are
  transactionally locked in primary-key order. The order and selected item
  are separately row-locked and revalidated after acquiring visit locks.
- Source order must be NEW, not a delivery/takeaway order; selected item
  must still be NEW or PENDING (no accepted, served, no-prep or cancelled items).
- Any current/historical **Payment**, including reversed entries, any
  OrderDiscount, InternetSession/InternetEntitlement, StockMovement on the
  order or item, or existing stock deduction **blocks splitting**.
- While global automatic stock deduction is active, splitting is blocked
  entirely. Moving/splitting a posted inventory movement requires a dedicated
  inventory correction policy and must never silently bypass stock.
- The snapshot line total must equal unit price times quantity. Historical
  estimated cost and margin snapshots, when present, must reconcile
  exactly; non-proportional historical corrections require manual review.
- No empty source order: if the last line's entire quantity is moving,
  use the existing whole-order transfer instead.
- A customer member-linked order can only split into a visit belonging to
  that same member.
- A per-form UUID operation token is recorded in ActivityLog. Visit locks
  serialize duplicate POSTs; replay of an identical token returns the
  original result without a second split. Reusing the token for different
  parameters is rejected.
- Audit details include source and new order IDs, item IDs, both visits,
  quantity before and moved quantity, original unit price and reason.
- No financial ledger postings, refunds, discounts or internet/network
  operations are created by this action.

## Full-page fallback and UX

The account drawer contains a collapsed `تقسيم عناصر هذا الطلب` editor,
so a normal staff shift is not crowded by advanced controls. HTMX returns
the refreshed visit panel, and a regular HTML POST keeps its existing
visit-detail redirect. Server permissions and validation apply to both.
Failed requests display an Arabic error; no partial mutation is retained.

## What still requires separate work

**Posted payment reversal is not shipped in this milestone.** The current
`core.services.posting.order_payments.reverse` sets reversal flags and may
add a cash movement, but does not construct a linked inverse PostingBatch
and PostingEntry pair. The approved D03 policy in
`docs/finance/domain-decisions.md` explicitly requires linked equal-and-
opposite postings on an open date with authorization and evidence. It must
be corrected and tested independently before creating a refund UI.

Other deferred complexities: splitting already paid orders, moving items
whose stock was posted, multi-stage preparation handoffs, and moving
membership/internet entitlements. These are deliberate safety boundaries,
not silent data loss.

## Release validation

One full Django/PostgreSQL CI checkpoint, targeted tests for quantity
splits, whole lines, balances, nullable historical snapshots, permissions,
paid/reversed orders, inventory settings, preparation, stale replays and
identical token retries.

After manual VPS deployment, use two sample open visits without auto-stock
postings; split one unit of a three-unit NEW line; verify two order totals,
actual source item quantities, and the ActivityLog. Try a paid item and
confirm it refuses without changing either balance. No MikroTik changes.
