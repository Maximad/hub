# Hub Suite — first-week operating mode (14 October 2026)

**Goal:** enable Hub Sweida staff to complete ordinary sales, stock and purchasing
work confidently with as few screens and decisions as possible. Real venue
operations began recently; the Django application itself has not yet completed
a supervised staff/customer rollout.

## The actual operating loop

1. **Set up:** active menu items and prices, physical stock counts, staff roles,
   cashier opening cash and approved financial accounts.
2. **Serve:** start a customer visit when sharing a bill, or a standalone order
   when a separate bill is intentional; send orders to bar/kitchen.
3. **Collect:** staff record actual cash/transfer receipts in the existing
   cashier workflow. Never mark an order as financially paid by changing its
   preparation status.
4. **Buy and receive:** save a purchase draft, confirm *actual* received
   quantities to change stock, then record a supplier payment in Finance if
   money actually left the cashbox or designated settlement account. No duplicate
   expense or manual cash movement for the same posted supplier payment.
5. **Finish shift:** compare orders, outstanding balances, counted cash,
   recorded supplier payments and physical stock exceptions before an authorized
   day close.

## What is implemented versus what still needs pilot acceptance

| Staff task | Current implementation | Required next evidence |
| --- | --- | --- |
| Customer order/POS | Role-gated POS and standalone/visit orders | One staff member submits and kitchen/bar sees it |
| Cash collection | Posted payment, cash movement and reconciliation audit | Cashier executes a legitimate sale, then checks balance |
| Shared bill | Visit-led orders and settlement | Two orders on one visit, correct close/no duplicate |
| Add inventory item | Admin/cashier form can set an **initial** quantity | Baseline count and units independently reviewed |
| Update existing stock | Stock movement form with audited adjustment workflow | Demonstrate authorized counts and corrections, no negative surprises |
| Record purchase | Inventory/Finance purchase forms | Staff can save correct draft quickly |
| Receive purchase | Dedicated confirmation and posting workflow | Receive once; stock and liability update once |
| Supplier payment | Separate finance settlement workflow | Only real authorized payment recorded; test approval limits |
| Bar/kitchen | Preparation board and waste/internal-use movements | Device test with correct roles |
| Day closing | Read-only daily summary + authorized closing | Opening cash, count and differences reconciled |
| Customer QR/Internet | Existing app and Internet release paths | Actual phones, devices and connection tests |

**Do not mark any live step above as PASS on the basis of CI or earlier
demonstration transactions.**

## Permission contract

- Bar/kitchen/floor employees should be able to **report physical counts**
  with low friction, without automatically obtaining authority to change stock
  valuation, supplier debt or cashier balances.
- Under the current code, the inventory list is readable by inventory-capable
  roles, while purchasing/initial item creation/normal adjustments require
  administrator or cashier privileges. Kitchen can enter approved
  waste/internal-use types. A simple, reviewable **stock count submission**
  workflow is a recommended next P0 feature; it is **not yet implemented**.
- No shared administrative account, no blanket finance capability grant,
  and no silent financial corrections.

## Specific usability defect already identified

The original Inventory and Finance purchase-entry forms rendered an
`amount_paid_syp` input even though both save handlers ignore that input.
A cashier could reasonably believe a supplier had been paid when only the
purchase draft was saved. The focused launch UX change removes that false
control and clearly identifies three actions: **save, receive, pay**.

This is a user-interface clarification only; supplier payment accounting,
posting permissions, idempotency and transaction logic are unchanged.

## Launch UX principles for subsequent batches

1. Show **one main next action per screen**. Hide status metadata and advanced
   tools behind secondary navigation, retaining full-page fallbacks.
2. Give staff immediate, specific confirmation: *draft saved*, *stock received*,
   *payment recorded*, or *needs manager approval*. Never say just “success”.
3. Keep language short and Arabic-first: **طلب جديد**, **قبض**, **استلام شراء**,
   **الكمية الحالية**, **إغلاق الوردية**.
4. Explain where money or stock will change **before** a consequential action.
   All money and inventory corrections must remain authorized and auditable.
5. Use existing role-specific routes; do not add a second bookkeeping system,
   new router settings or unverified automatic stock deduction.
6. Friendly design should mean clear progress, forgiving forms, good mobile
   layout, and useful feedback—not point rewards for financial transactions.

## First assisted staff session (60–90 minutes)

- 10 minutes: staff log in individually on their actual phone/tablet.
- 15 minutes: one example order, status, handoff and cashier view.
- 15 minutes: list/count essential starting inventory and verify units.
- 15 minutes: one **staging** purchase from draft through receipt and payment
  with the correct authorization; no fabricated cash event in production.
- 10 minutes: manager reviews the daily summary and exception handling.
- 10 minutes: watch staff repeat the three most common tasks without help.
- Remaining time: record the most severe navigation/failure issues.

Do a quiet, controlled first real shift only after staging checks, with backup,
rollback path and manual fallback ready. Record **PASS / FAIL / NOT TESTED** for
each device/role journey, and prioritize fixes observed in actual use.

## Immediate next development queue

**P0-1 (current PR):** remove misleading supplier-payment inputs from both
purchase draft forms, clarify receipt/payment steps, link authorized staff to
the actual payment screen, add non-posting regression tests.

**P0-2:** opening stock count and review for existing items. Staff enter actual
counts quickly; a designated approver reviews deltas and applies existing
audited stock movement, preserving ledger policy. Confirm unit conversion
rules before implementing batch changes.

**P0-3:** role-based **Today** workspace with only the main actions relevant to
each staff member (order, prepare, collect, stock, purchase, shift), preserving
advanced pages. Validate with actual users before adding animations.

**P0-4:** lightweight opening/closing checklist and a simple explanation of
cashbox totals, unpaid orders and unrecorded supplier payments.

**P1:** investigate staff-observed friction, assisted onboarding and quick
search; later consider richer visual design and subtle interaction polish.

**Do not:** run automatic prelaunch resets, fabricate payments, change
MikroTik/RouterOS, enable unapproved finance flags, or merge/deploy P0 changes
without green full Django/PostgreSQL CI.
