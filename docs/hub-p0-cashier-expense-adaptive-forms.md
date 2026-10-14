# Hub Suite — P0 cashier and expense adaptive forms
Date: 2026-10-14
Release state: PR only; no production deployment, account configuration or migrations.

## Goal
Reduce form cognitive load for Hub Sweida staff without modifying payment
posting, partial-approval policy, currency conversion or financial history.

## Staff expense entry

The essential inputs are date, expense category, a short title, original
amount/currency and **intent**:
- **Draft** — saving the details does not post a payment or create cash
  movement. Default selection.
- **Paid now** — reveal existing payment-method and actual financial-account
  choices. The posting service must validate the submitted account and
  permissions. A cashbox projection is created only by existing policy.
- **Approved liability** — reveal an actual liability account; existing
  finance/approval policy remains authoritative. D05 is still subject to
  policy review, so this interface is *not* approval of D05 production posting.

The selected beneficiary type controls the registered-vendor versus
manual-payee input. Supplier name remains required when the manual recipient
is chosen; the existing server service validates it. Invoice, media receipt
and long notes live in an optional native `<details>` section. The server
preserves submitted values and errors on invalid POST. Common currency
component now retains value/currency/acknowledgment on validation errors.

All dependent sections are present in the HTML if JavaScript is off;
scoped enhancement disables irrelevant fields only after initialization.
No account type is guessed from a payment label, and client-side
disclosure never substitutes for server-side authorization.

Expense status forging for unsupported states (e.g. `cancelled` on a new
expense) now yields a visible validation error instead of silently creating
a draft. Existing D01/D04 expense posting/reversal logic is untouched.

## Cashier

- **Standalone full page**: only offer collection when remaining > 0; the
  original `#payment-amount` selector did not correspond to the included
  currency component. It now binds `[data-currency-amount]`, observes the
  selected currency, and uses the actual remaining balance to reveal manager
  credentials for partial SYP payments.
- **Drawer**: keep actual settlement, method and idempotency fields. Move
  optional notes into `<details>`, retaining the existing payment body and
  authorization controller. Manager credentials remain present without JS
  so staff can still request approval with a full-page fallback.
- **Visit full page**: preserve separate `payment` and `settle_close`
  actions, Internet-finalization warning and actual visit accounting. Show
  manager credentials when needed. Optional notes are collapsed.
- Never infer that USD is less than SYP remaining: foreign-currency conversion
  remains authoritative at the server. The drawer retains its existing
  required payment, visit allocation and close-posting contract.

## Tests and acceptance

- Real Django render with legacy data, Arabic/English and missing media;
  non-finance users denied.
- Draft, paid, approved liability posting regression tests remain in the
  repository; no changes to those service calls.
- Invalid expense returns visible errors and original fields, and creates
  no cash/payment records. Unsupported status is rejected.
- Full page with zero outstanding hides payment form; drawer and visit retain
  partial-approval credentials and optional notes.
- Full PostgreSQL CI is a **merge gate**, not proof of usability on phones.
- Before the later batched deployment, verify cashier and expense forms on a
  staff phone and tablet using only staging test transactions. Deploy through
  established backup/restore gates; do not alter MikroTik, production ledger
  flags, production data or historical payment records.
