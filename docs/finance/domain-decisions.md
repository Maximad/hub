# Finance domain decisions

## Authority and confirmation state

On **2026-08-11**, owner/finance lead **Maxim Abou Diab** approved D01-D04 and D12-D14. On **2026-08-22**, D07, D08, D09, and D11 were approved for Hub Sweida. D05, D06, and D10 remain **UNCONFIRMED / POSTING BLOCKED**; no policy is implied for them.

The sole operating unit is **Hub Sweida / هَب السويداء**. Approved launch
accounts use the existing blank/global `business_unit`; rooms are not accounting
units. `business_date` is the recognition and close date in `Asia/Damascus`.

## Common controls (apply to all decisions)

* **Business-unit scope:** an account with a non-empty `business_unit` is usable
  only by that unit. Blank/global scope must be explicitly confirmed; it is not
  a fallback for an unknown unit.
* **Performance and approval:** authentication never implies approval. Actor and
  approver are recorded separately; where approval is required they must be
  different people unless the confirmed decision explicitly permits self-approval.
* **Recognition and dates:** `business_date` controls the ledger period and
  closing. `created_at`, `posted_at`, payment time, and receipt time are audit
  timestamps and never silently replace it. Posting to a closed account/date is
  blocked.
* **Cancellation:** an unposted draft may be cancelled without ledger entries.
  A posted operation is immutable and is corrected by a linked, equal-and-
  opposite reversal on an open business date, with actor, approver, and reason.
* **Reporting/settlement:** reports use posted and linked reversal batches only.
  Settlement never changes the original recognition date or overwrites history.

## The fourteen owner/finance-lead decisions

Every item below deliberately contains the six requested answer dimensions.
`TBD` is a blocking value.

### D01 — Cash sale collection
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** on the order `business_date`, debit active `cash:main` and credit active `revenue:operating`, both global. Admin/finance may self-approve with actor and approver audit evidence. A posted collection is immutable and can only be undone through D03. Cash settlement is included in daily close.

### D02 — Non-cash sale collection
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** on the order `business_date`, debit the active clearing account matching the tender (`clearing:card`, `clearing:bank`, `clearing:mobile`, or `clearing:external`) and credit `revenue:operating`. All are global. Admin/finance may self-approve with explicit audit evidence. Settlement never changes recognition date.

### D03 — Customer refund or payment reversal
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** a posted refund or void creates one linked, equal-and-opposite reversal on an open `business_date`. The original posting is never edited or deleted. Actor, permitted approver, reason, original receipt, and tender link are mandatory audit evidence.

### D04 — Immediate expense payment
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** on `business_date`, debit the applicable expense category account (launch default `expense:operating`) and credit the explicitly selected active financial account. Admin/finance may create and self-approve, with both identities recorded; other roles cannot approve. Draft cancellation is allowed, while a posted expense requires a linked reversal and reason.

### D05 — Expense liability approval
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** debit expense/accrual; credit liability account; mappings: TBD.
**Perform/approve:** preparer and finance approver: TBD.
**Recognition:** invoice, receipt, service, or approval event: TBD.
**Posting/close date:** invoice/service/business date rule: TBD.
**Cancellation/reversal:** reject draft or reverse approved liability: TBD.
**Reporting/settlement:** aged liabilities, supporting document, due date: TBD.

### D06 — Expense liability settlement
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** debit D05 liability; credit selected asset/clearing; scope: TBD.
**Perform/approve:** payment maker, signatory, thresholds: TBD.
**Recognition:** settlement only; expense remains on D05 date, subject to confirmation.
**Posting/close date:** payment business date: TBD.
**Cancellation/reversal:** linked settlement reversal, without erasing D05: TBD.
**Reporting/settlement:** payable allocation and bank/cash reconciliation: TBD.

### D07 — Purchase receipt and supplier liability
**Status/confirmation:** CONFIRMED — 2026-08-22.
**Executable policy:** goods receipt recognizes only actual received quantity at original `PurchaseItem.unit_cost_syp`, on `PurchaseReceipt.business_date`: debit global `inventory:purchases`, credit global `payable:suppliers`. Each partial receipt posts independently. The authorized operational receiver triggers the system posting without discretionary finance approval. Stock and over-receipt controls remain unchanged; historical operational receipts are not backfilled.

### D08 — Supplier payment
**Status/confirmation:** CONFIRMED — 2026-08-22.
**Executable policy:** on settlement `business_date`, debit global `payable:suppliers` and credit only global `cash:main` or `bank:main` (plus the D11 methods below). Partial payment is allowed only up to purchase-specific posted, unreversed D07 liability less posted D09 reversals and active unreversed payments. Old unposted receipts and unreceived value cannot be paid. Admin/finance may self-approve below 50,000 SYP_NEW; at or above it a different active admin approves. A posted payment is immutable; correction is a linked equal-and-opposite reversal on an open date with a reason. Cash payment and reversal project OUT and IN supplier-payment cash movements respectively; other sources do not.

### D09 — Purchase return / receipt reversal
**Status/confirmation:** CONFIRMED — 2026-08-22.
**Executable policy:** physically valid returned stock is valued at original item cost. Up to outstanding purchase-specific payable, debit `payable:suppliers` and credit `inventory:purchases` on the return business date. If return value exceeds unpaid liability, physical return completes, only the matched amount posts, and the excess becomes `paid_purchase_return_requires_finance_resolution`. No supplier receivable, credit note, cash refund, price variance, or negative payable is inferred. Existing consumed-stock protection remains.

### D10 — Inventory adjustment, waste, or production consumption
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** inventory asset and waste/COGS/variance/production accounts
by reason and unit: TBD.
**Perform/approve:** counter, recorder, tolerance-based approver: TBD.
**Recognition:** count approval, waste event, or production completion: TBD.
**Posting/close date:** movement/count business date: TBD.
**Cancellation/reversal:** opposite stock and value movement; negative stock policy: TBD.
**Reporting/settlement:** quantity/value variance and recipe/production reconciliation: TBD.

### D11 — Owner-paid supplier purchase
**Status/confirmation:** CONFIRMED — 2026-08-22.
**Executable policy:** an owner payment expecting reimbursement debits `payable:suppliers` and credits global `payable:owner`; a permanent contribution debits `payable:suppliers` and credits global `equity:owner_contribution`. Both are supplier settlements under the D08 threshold, create no Hub cash movement, and never use `clearing:owner_paid`, which remains inactive for new postings.

### D12 — Cash correction, deposit, or withdrawal
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** restricted to admin/finance, against explicit active global accounts, on `business_date`. Every operation requires a reason and actor/approver audit evidence. Correction of a posted movement uses a linked reversal; no posted record is edited or deleted.

### D13 — Transfer between financial accounts
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** restricted to admin/finance and explicit active same-currency accounts. Below `50,000 SYP_NEW` no second person is required. At or above `50,000 SYP_NEW`, a different active admin must approve. A reason and `business_date` are required; correction is a linked balanced reversal of both legs.

### D14 — Account/day close and reopen
**Status/confirmation:** CONFIRMED — Maxim Abou Diab, 2026-08-11.
**Executable policy:** finance/admin closes each active closable account for `business_date` only after entering an actual cash count. Posting to that closed account/date is blocked. Only an admin may reopen, with a mandatory reason and append-only close revision/audit event. Close itself recognizes no revenue or expense.

## Conversion register

| Artifact | Confirmed conversion | Current safe state |
|---|---|---|
| Account seed data | D01-D04 and D12-D14 via `bootstrap_launch_finance`; D07-D09/D11 via `bootstrap_purchase_finance` | D05/D06/D10 candidates and `clearing:owner_paid` remain inactive. |
| Posting rules | D01-D04, D07-D09, D11-D14 | D05/D06/D10 posting remains blocked; purchase resolution fails closed. |
| Permissions | Admin/finance self-approval; D13 threshold exception | Other roles cannot approve launch finance operations. |
| Acceptance-test fixtures | Role, closed-date, inactive-account, threshold, purchase and bootstrap fixtures | D05/D06/D10 blocking fixtures remain in force. |

After confirmation, each implementation change must cite decision IDs in its
migration/rule/test, activate only the approved codes and scopes, and add both a
successful fixture and rejection fixtures for wrong unit, role, date, state, and
account. Partial confirmation must not weaken the remaining blocks.
