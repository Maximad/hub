# Expense posting service contract (phase 1)

All staff, admin, import, and future API writes must call
`core.services.posting.expenses`; saving a posted `Expense`, generated
`CashMovement`, or `PostingBatch` directly is unsupported.

## Commands and invariants

Callers supply a `PostingContext` containing the authenticated actor, an
idempotency key stable across retries, the business date, channel, and safe
request metadata. Multi-stage callers derive deterministic suffixes from that
key. The supported commands are `create_draft`, `approve_liability`,
`pay_immediately`, `settle_liability`, `cancel_unposted_draft`, and
`reverse_posted_expense`.

The service locks the source and applicable account/close rows, rejects closed
periods, and stores a durable command receipt. A retry with the same key and
command returns the original result; reuse for a different command fails.
Posted history is immutable: corrections use a linked reversal rather than an
update or delete. `Expense.paid_from`, existing reports, `CashMovement`, and
existing URLs remain compatibility projections during rollout.

## Imports and future APIs

Use `core.services.posting.expense_import.import_expense(payload, context,
account_mapping=...)`. Required fields are `business_date`, `category_code`,
`title`, and `amount_syp`. A paid row also requires `payment_method` and an
`account_ref`. `account_mapping` must explicitly map that external reference to
a stable `FinancialAccount.code`; the adapter never infers an account or
business-unit owner from Arabic or English display names.

The first phase accepts draft and immediately paid rows only. Liability
approval and settlement should be expressed as separate API commands so actor,
approver, idempotency key, and audit intent remain unambiguous.

## Reconciliation

Run `python manage.py reconcile_finance --scope=expenses --format=json`. This is
a read-only comparison of legacy expense/cash projections, posting batches,
idempotency receipts, and source-linked audits. Use `--check` in deployment
gates to return a non-zero status when discrepancies exist. The command never
repairs or backfills production data.
