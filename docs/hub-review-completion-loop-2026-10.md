# Hub Suite: complete an existing operational follow-up

Base reviewed: `d4dcd5e8b429472fcb7c868cee151e0a32801cf2` (21 October 2026).

## Review of the four daily workflows

| Workflow | Existing implementation retained | Gap addressed in this package |
| --- | --- | --- |
| Sales | Staff workspace, POS, preparation, cashier and visit account settlement | A zero-price follow-up had no action link. It now opens the exact product in existing menu tools, including the existing price preview/apply flow. No sale, preparation, discount or payment rules change. |
| Purchasing | Draft purchase, explicit receiving, separate supplier payment, receipt/movement provenance | Receipt review linked to a finance-only page despite being routed to inventory-capable duty staff. It now opens inventory purchase detail. Financial corrections remain with authorized finance staff. |
| Stock count | Authorized count, unit conversion, signed preview, atomic confirmation and audit even for unchanged counts | After a real count, the duty holder could not re-evaluate their case without a manager's global sync. A single-case evidence check now closes that loop. |
| Internet | Session lifecycle, durable network outbox, billing and session detail | A proven activation-failure case uses the same evidence check after ordinary session recovery/end. The check performs no network operation or billing action. |

PR #332 was also inspected. Its open draft adds a read-only handover briefing, and its PostgreSQL CI passed. This package starts from main and does not copy, modify, merge or depend on that briefing. PR #327 remains closed and is not reimplemented.

## Resulting staff flow

Each existing review task shows a reason, the evidence needed for completion, and a permission-checked link to its original record. The Operations duty holder or authorized manager can select **التحقق من الاكتمال** after working in that record.

The preview reads one existing case and current source evidence without writes. If the state changes, an explicit confirmation is available. Confirmation re-reads the source and permissions, and updates only that case and its current-day task projection. No unrelated discovery or queue synchronization runs. Resolved cases can similarly reopen if their original condition returns.

Confirmation uses an actor/day/case-bound signed preview expiring after ten minutes. Changed evidence requires another preview. The day lock serializes confirmation with duty transfer and closing, and the case lock makes duplicate/concurrent confirmation harmless. Case, task projection and audit entry share a transaction. Missing or incompatible source records require management review rather than silently closing a case. Historical closed-day projections remain unchanged.

Source links follow destination permissions. Inventory-only staff can inspect a receipt without finance access, and denied capabilities do not produce unusable source links. Ordinary staff also need checked-in, accepted Operations duty. A proposed handover recipient gains no access until acceptance. Each destination continues to enforce its own permission.

## Validation

Automated coverage includes the real unchanged-count flow, read-only preview, unresolved evidence, exact-product filtering, receipt destination permissions, provider/unauthorized denial, capability revocation, checkout, accepted duty transfer, invalid/expired/misbound tokens, closed days, missing sources, changed evidence, duplicate/concurrent confirmation, CSRF and transaction rollback on audit failure. Existing review-case tests continue covering recipe, receipt and Internet evidence and historical import exclusions.

Full PostgreSQL CI results are recorded in the pull request. The local Django system check passes. This workspace cannot switch to a non-root database user, so database-dependent validation runs in the repository's existing PostgreSQL 16 CI job rather than SQLite.

## Remaining work

- This is a bounded improvement to the staff completion loop, not a claim that all four workflows have completed staff acceptance. No manual operational testing was requested or performed.
- A positive stored price remains the existing completion condition. It is not a new approval ledger or final cost/provenance certification.
- Source guidance deliberately uses current existing evidence rules. An unverified historical balance remains uncounted; receipt corrections require their original lifecycle, not edits to posted history.
- Deeper quantity provenance (last physical count versus later adjustments), remaining purchase/session error UX, and final accounting reconciliation remain subsequent small packages.
- Production SHA and parity with main remain unverified. Deployment and any production database operation require explicit user approval.

## Risk and rollback

No migrations, new models, background workers, automatic stock deductions, purchasing, forecasts, RouterOS changes or production writes. The only new persistence is an explicitly confirmed existing review-state transition, current-day projection and audit row. Revert this package to remove the action and UI. Keep existing cases/audit evidence; do not rewrite source history to roll back a review.
