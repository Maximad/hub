# Hub Suite D10: direct inventory consumption requests (draft-only implementation)

**D10 remains UNCONFIRMED / POSTING BLOCKED.** This module does not deduct physical
stock, create stock movements, post financial entries, charge owners, or reserve
inventory. It does not change the disabled `staff_inventory_movement_new` URL.

## Why this release exists

POS hospitality/personal use is already categorized under `OrderConsumption`
(PR #310). This milestone adds a *different* path for a loose inventory item
such as a bottle or 1.5 kg of ingredients taken without a customer order.
It captures who requested it, why, category (Hub hospitality, owner personal,
staff, event), recorded source stock balance, estimated cost if known, optional
beneficiary/event information, and base-unit quantity. It is an **unposted
operational request**, NOT proof of completed physical inventory deduction.

- Existing physical unit converter provides kg ↔ g and litre ↔ ml.
- Staff with the inventory capability may request staff consumption; managers
  with delegated partial-payment approval can also request Hub hospitality or
  event use. Personal owner use remains **superuser-only**.
- Staff see their own requests; non-owner managers see non-personal requests.
  Owner sees all, including private personal records.
- Creation is idempotent by client-generated UUID; conflicts fail closed.
  Cancellation is logged with actor/reason and no stock or accounting impact.
- Stock at request time is a snapshot, **not a reservation**; a second request
  does not consume the remaining balance. The future posting stage must lock,
  recheck and compare with recipe consumption so it cannot double-deduct.
- Missing estimated cost stays unknown rather than invented.

## Policy questions requiring the owner's approval before posting

D10 in `docs/finance/domain-decisions.md` has six unanswered dimensions.
**Proposals, not decisions:**

1. **Account mapping and valuation:** posting expense to Hub hospitality,
   staff benefit or event marketing versus owner equity/receivable for personal
   use; value at approved cost valuation, not the selling price.
2. **Who approves:** request may be prepared by any allowed staff role;
   actual stock and finance posting should require separate authorized approval
   unless the owner expressly approves self-approval thresholds.
3. **Recognition:** at confirmed physical handover/consumption, not when a
   draft is submitted.
4. **Business date / period close:** use Damascus local business date and
   block posting to already closed account/date.
5. **Reversal:** immutable linked opposite stock/value movement on an open
   business date, with a reason and approver; no silent delete.
6. **Reconciliation:** prevent duplicate deduction of recipe/production items,
   negative stock, and unlinked amounts. Track per-category quantity/value
   separately from cash sales and owner reimbursement.

After formal approval, implement a separate idempotent posting service,
authorization matrix, ledger mapping, reversal workflow and full PostgreSQL
tests. The existence of these drafts is **not** authorization to post them.

## Release safety

Before an actual production deploy, explicitly explain that this screen
does not alter stock. Pilot with test items; verify the request's stock balance
and journals are unchanged. It may be safer to defer public staff rollout
until D10 posting is approved. No VPS/deployment or MikroTik changes in this PR.

## Consolidation of PRs 312 and 313

The unified implementation uses PR 312's `InventoryConsumptionRequest`, service
layer, exact-payload idempotency, private owner records, event label, cost/stock
snapshots and author-or-owner cancellation. PR 313 contributes operational review,
whole-container validation and a live conversion preview. Its parallel model,
route and migration are deliberately not included. Use this PR instead of merging
312 and 313 separately. Neither original migration has been deployed according to
the handover; migration 0058 is consolidated before release, not an upgrade path
for databases that have independently installed either draft.

Managers with inventory access and the owner can acknowledge a pending request
with a note. Personal requests remain owner-only for both reading and actions.
Review records actor and timestamp and appends an audit event. It never approves
posting. Repeated review is a no-op; cancelled requests cannot be reviewed.
The original author or owner can cancel a reviewed request without erasing its
review evidence. Former owners cannot cancel private records after losing access.

No D05, D06 or D10 policy is activated. Stock, cash, payment and ledger services
are unchanged. Whole pieces, bottles, packs and boxes require integer quantities;
no container-to-volume mapping is inferred. Mass and volume use the existing exact
conversion service, with a browser preview that is informational only.

Validation includes role and privacy boundaries, the review/cancel lifecycle,
whole bottles, event references, concurrent duplicate creation and review, and
unchanged stock/ledger balances. Full PostgreSQL CI and narrow-screen Arabic
review remain release gates. Do not merge or deploy automatically.

## Operational upgrade approved 2026-10-19

The draft-only pilot is superseded by the limited stock-only policy in
`domain-decisions.md`. The same URL now supports actual issue confirmation,
linked full return/correction and monthly direct-consumption reporting. Existing
requests are not auto-issued. Review-only history remains readable; new actions
use explicit issue confirmation. No financial posting is authorized.
