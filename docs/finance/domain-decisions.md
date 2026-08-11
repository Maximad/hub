# Finance domain decisions

## Authority and confirmation state

On **2026-08-11**, owner/finance lead **Maxim Abou Diab** approved only
**D01, D02, D03, D04, D12, D13, and D14** for the Hub launch. No other
decision is implied or approved. D05-D11 remain **UNCONFIRMED / POSTING
BLOCKED**, and their candidate accounts and posting paths must remain inactive.

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
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** candidate debit `inventory:purchases`, credit
`payable:suppliers`; both remain inactive until unit and ownership are confirmed.
**Perform/approve:** receiver, purchase approver, quantity/value tolerances: TBD.
**Recognition:** current candidate is goods receipt; owner confirmation: TBD.
**Posting/close date:** receipt business date candidate: TBD.
**Cancellation/reversal:** return/reverse only if stock is available; override: TBD.
**Reporting/settlement:** receipt-to-PO/invoice match, inventory and payable reports: TBD.

### D08 — Supplier payment
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** debit `payable:suppliers`; credit active selected cash,
bank, or owner-paid clearing account; unit allocation: TBD.
**Perform/approve:** payment maker, approver, threshold, self-approval: TBD.
**Recognition:** liability reduction on payment/settlement event: TBD.
**Posting/close date:** payment business date: TBD.
**Cancellation/reversal:** linked payment reversal; paid-return handling: TBD.
**Reporting/settlement:** supplier allocation, remittance, bank/cash match: TBD.

### D09 — Purchase return / receipt reversal
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** reverse D07 inventory/payable; price variance account: TBD.
**Perform/approve:** inventory actor and finance approver: TBD.
**Recognition:** dispatch, supplier acceptance, or credit-note date: TBD.
**Posting/close date:** open return business date; original-period policy: TBD.
**Cancellation/reversal:** linked to receipt; consumed-stock exception is blocked.
**Reporting/settlement:** stock return, supplier credit, payable allocation: TBD.

### D10 — Inventory adjustment, waste, or production consumption
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** inventory asset and waste/COGS/variance/production accounts
by reason and unit: TBD.
**Perform/approve:** counter, recorder, tolerance-based approver: TBD.
**Recognition:** count approval, waste event, or production completion: TBD.
**Posting/close date:** movement/count business date: TBD.
**Cancellation/reversal:** opposite stock and value movement; negative stock policy: TBD.
**Reporting/settlement:** quantity/value variance and recipe/production reconciliation: TBD.

### D11 — Owner contribution or owner-paid purchase
**Status/confirmation:** UNCONFIRMED / POSTING BLOCKED — owner/finance lead and date: TBD.
**Accounts and scope:** candidate `clearing:owner_paid` versus owner equity/loan;
legal owner and unit allocation: TBD.
**Perform/approve:** recorder, owner confirmation, finance approval: TBD.
**Recognition:** contribution, reimbursable liability, or settlement timing: TBD.
**Posting/close date:** funding/payment business date: TBD.
**Cancellation/reversal:** linked reversal; reimbursement treatment: TBD.
**Reporting/settlement:** owner statement, reimbursement and equity/loan reporting: TBD.

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
| Account seed data | D01-D04, D12-D14 launch accounts via `bootstrap_launch_finance` | D05-D11 candidate accounts remain inactive. |
| Posting rules | D01-D04, D12-D14 only | Purchase account resolution now rejects missing, inactive, or wrong-type accounts. |
| Permissions | Admin/finance self-approval; D13 threshold exception | Other roles cannot approve launch finance operations. |
| Acceptance-test fixtures | Role, closed-date, inactive-account, threshold, and bootstrap fixtures | D05-D11 blocking fixtures remain in force. |

After confirmation, each implementation change must cite decision IDs in its
migration/rule/test, activate only the approved codes and scopes, and add both a
successful fixture and rejection fixtures for wrong unit, role, date, state, and
account. Partial confirmation must not weaken the remaining blocks.
