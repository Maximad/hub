# Hub Suite — First-Week Integration Review
**Reviewed:** 14 October 2026, against GitHub `main` commit
`ea3772373fe1352345b72e503f0a4b096a5d2a79`
(PRs #284, #285, #287, #289 and #290 already merged).

**Status:** Code integration review and targeted fixes proposed. **Not a production
acceptance certificate.** This review performed no VPS deployment or changes
to customer financial history. The subsequent PR/CI status must be checked
before treating the receipt fix as merged.

## Intended operating chain

| Stage | Current real route | Gate / expected outcome |
| --- | --- | --- |
| Start work | `/staff/` | Effective role shows up to four relevant daily links |
| Sell | Staff POS and existing table/visit customer workflows | Shared visit vs standalone bill never inferred from table alone |
| Collect | Existing cashier order/visit posting | Remaining balance, actual method, and partial approval remain server-checked |
| Add initial counts | Inventory → جرد المخزون | Only full-admin, effective تشغيل وكاشير, or individually authorized counter; operational quantity correction only |
| Buy | Inventory → new purchase | One or more valid lines create a draft, **zero** receipt/payment writes |
| Receive | Inventory detail or Finance purchase detail | Actual delivered quantity, receipt journal and inventory move posted once |
| Pay supplier | Finance → purchase detail | **Only when a posted payable exists**, never simply by saving a draft |
| Expenses | Finance → expense form | Draft/paid/approved intent; real money/account behavior remains posting-service controlled |
| Close day | Existing close-day/finance summaries | Reconcile physically counted cash; do not certify on unit tests alone |

## Findings from source review

**P0 — partial receiving presented wrong defaults.** Prior to this review,
Inventory and Finance displayed each purchase line's original ordered
quantity every time the page loaded, even after a partial receipt. An
employee would attempt to receive the same units again and the posting
service would correctly reject an over-receipt, but the form did not show
what remained. Both pages now share a read-only receipt-progress adapter:
ordered, already received in non-reversed receipts, and remaining.
Only remaining quantities are prefilled; a finished or cancelled purchase
does not offer another receipt form. Source-of-truth posting logic is
unchanged. A kitchen worker who may read purchase details cannot see the
receive form without existing cashier/admin rights.

**P0 — Arabic localization rendered invalid numeric HTML attributes.**
The first full PostgreSQL test pass for this integration PR revealed that
Arabic-localized decimals such as `3,000` were rendered into
`<input type="number" value=... max=...>`. For HTML numeric controls, these
attributes must use a period as decimal separator. The shared receipt
field template now explicitly `unlocalize`s both machine-readable
attributes while keeping Arabic-localized quantity descriptions. The
integration assertion enforces a browser-valid `value="3.000"` and
`max="3.000"` in both entry screens.

**P0 — supplier-payment form appeared without debt.** Previously the
Finance purchase page showed an actionable supplier-payment form even
on a draft with no recognized payable, or after everything had been paid.
It now offers payment only when the read-only posted
`financial_state.outstanding` is positive and the purchase is not
cancelled. Otherwise it explains that goods must be received or prior
settlements checked. This changes presentation only: the payment service
already rejects payments without recognized outstanding liability.

**Existing boundaries preserved:**
- Physical counts modify quantities with audit entries, but do not
  recognize opening monetary value.
- Manual waste / adjustment workflow D10 is blocked pending accounting
  policy approval. Issue #288 remains open.
- Purchase receipt and supplier payment remain separate, audited events.
  The new adapter counts only **non-reversed** receipt lines.
- Currency conversion, cashier partial approval, receipt posting,
  supplier-payment approval thresholds and payment reversal are enforced
  by existing services and not reimplemented in JavaScript.
- The Operations quick-start uses effective capabilities and does not
  bypass server view authorization.

## Tests added to the current PR

`core/tests/test_first_week_purchase_integration.py`:
- Draft creates no cash, supplier payment or stock movement.
- First legitimate partial receipt changes stock, posts recognized
  supplier liability, and leaves unpaid balance.
- Both receipt screens display remaining (not original) quantities.
- Second receipt completes outstanding quantity without over-receipt.
- Receipt forms disappear after complete receipt or cancellation.
- Supplier-payment form is absent on draft, visible for payable liability.
- Kitchen role cannot access receive/pay controls or post a receipt.

Existing suites for role-aware homepage, cashier visited/standalone
settlements, stock-count concurrency, expense drafts/paid and D10 safe
blocking remain required CI gates. These tests are run on an isolated CI
PostgreSQL database, **never** on production.

## Unverified before staff rollout

1. **Deployment state:** verify deployed revision separately; do not infer
   from merged GitHub `main`.
2. **Operational catalog:** real prices, purchasable inventory items,
   vendor list, units, opening cash, payment accounts, and actual recorded
   stock counts are not verified by this static review.
3. **Money acceptance:** in a separate staging environment confirm
   partial visit payment, standalone payment, supplier settlement, expense
   paid/draft/approved, idempotent retry, close-day balance and reversal
   with representative amounts.
4. **Mobile experience:** test on actual cashier, kitchen and service
   phones/tablets (Arabic RTL; weak network; on-screen keyboard; field
   dependencies; drawer fallback). Watch staff perform tasks unaided.
5. **Customer flows:** separate Android/iOS devices must check QR ordering,
   visit association, order preparation and Internet final billing.
   Never infer device Wi-Fi behavior from Django unit tests.
6. **Physical stock correctness:** inventory amounts entered by actual
   authorized operators, opening balance accounting policy, and D10
   waste/consumption policy remain operational decisions.
7. **Launch prerequisites:** backup and verified restore, effective
   per-user accounts/roles, configured currency rates, allowed domains,
   safe deployment release commit and rollback path.

## Suggested first supervised pilot (one shift)

Give staff actual user accounts. Begin with existing stock items and
perform three unscripted paths: order → preparation → payment; purchase
draft → partial delivery → supplier due; count inventory → review
difference. Do not create test payments on the production database.
Record route, role, device, observed difficulty, severity and correction.
Compare accounting totals with actual cash/stock after the shift. A
successful test suite is a code gate, **not** proof that the cashier or
customer has used the system successfully.

## Release decision

**Code-side gate:** green full Django/PostgreSQL CI for the proposed PR,
review, and merge. **Production gate:** separate owner authorization,
verified backup/restore, deployed SHA check and on-device pilot. Until
then leave production, financial flags, MikroTik/RouterOS and historic
orders/payments untouched.
