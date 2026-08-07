# Finance domain decisions

## Authority and confirmation state

This register is the hand-off to the **owner/finance lead**, who is the only
person authorised to confirm these policy choices. No answers from that person
were supplied with this change. Consequently all fourteen decisions below are
recorded as **UNCONFIRMED / POSTING BLOCKED**; this document does not manufacture
business policy on the owner's behalf.

A decision becomes confirmed only when the owner/finance lead replaces its
status with `CONFIRMED`, records their name and date, and completes every field.
Account activation and posting-rule deployment happen only after that review.
Until then, candidate accounts remain inactive and a missing, inactive, or
wrong-type account is an error—not an invitation for code to choose one.

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
**Status/confirmation:** UNCONFIRMED — owner/finance lead and date: TBD.  
**Accounts and scope:** debit `cash:<business-unit>`; credit revenue, tax,
discount and rounding accounts: TBD; unit mapping: TBD.  
**Perform/approve:** cashier may perform; approval threshold and approver: TBD.  
**Recognition:** sale, tax, discount, and rounding recognition event: TBD.  
**Posting/close date:** order business date proposed; owner decision: TBD.  
**Cancellation/reversal:** void-before-post and refund/after-post policy: TBD.  
**Reporting/settlement:** tender reconciliation, cash close, and tax reporting: TBD.

### D02 — Non-cash sale collection
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** debit bank/card/external clearing and credit D01 revenue
accounts; provider and unit mappings: TBD.  
**Perform/approve:** cashier; exception/partial-payment approval: TBD.  
**Recognition:** sale versus provider-settlement timing: TBD.  
**Posting/close date:** order business date versus provider date: TBD.  
**Cancellation/reversal:** void, chargeback, and refund accounts: TBD.  
**Reporting/settlement:** provider batch, fees, variance, and bank matching: TBD.

### D03 — Customer refund or payment reversal
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** exact reversal of D01/D02 accounts; refund tender and unit: TBD.  
**Perform/approve:** initiator, limits, and independent approver: TBD.  
**Recognition:** when refund is authorised versus paid: TBD.  
**Posting/close date:** open reversal business date; backdating policy: TBD.  
**Cancellation/reversal:** linked reversal only; reversal-of-reversal policy: TBD.  
**Reporting/settlement:** refund reason, original receipt link, tender settlement: TBD.

### D04 — Immediate expense payment
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** debit expense category; credit selected cash/bank/clearing;
category-to-code and unit mappings: TBD.  
**Perform/approve:** preparer, spending limits, approver, self-approval: TBD.  
**Recognition:** payment versus invoice/service date: TBD.  
**Posting/close date:** controlling business date: TBD.  
**Cancellation/reversal:** draft cancellation; posted linked reversal: proposed, TBD.  
**Reporting/settlement:** receipt, payee, category, unit, and tender close: TBD.

### D05 — Expense liability approval
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** debit expense/accrual; credit liability account; mappings: TBD.  
**Perform/approve:** preparer and finance approver: TBD.  
**Recognition:** invoice, receipt, service, or approval event: TBD.  
**Posting/close date:** invoice/service/business date rule: TBD.  
**Cancellation/reversal:** reject draft or reverse approved liability: TBD.  
**Reporting/settlement:** aged liabilities, supporting document, due date: TBD.

### D06 — Expense liability settlement
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** debit D05 liability; credit selected asset/clearing; scope: TBD.  
**Perform/approve:** payment maker, signatory, thresholds: TBD.  
**Recognition:** settlement only; expense remains on D05 date, subject to confirmation.  
**Posting/close date:** payment business date: TBD.  
**Cancellation/reversal:** linked settlement reversal, without erasing D05: TBD.  
**Reporting/settlement:** payable allocation and bank/cash reconciliation: TBD.

### D07 — Purchase receipt and supplier liability
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** candidate debit `inventory:purchases`, credit
`payable:suppliers`; both remain inactive until unit and ownership are confirmed.  
**Perform/approve:** receiver, purchase approver, quantity/value tolerances: TBD.  
**Recognition:** current candidate is goods receipt; owner confirmation: TBD.  
**Posting/close date:** receipt business date candidate: TBD.  
**Cancellation/reversal:** return/reverse only if stock is available; override: TBD.  
**Reporting/settlement:** receipt-to-PO/invoice match, inventory and payable reports: TBD.

### D08 — Supplier payment
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** debit `payable:suppliers`; credit active selected cash,
bank, or owner-paid clearing account; unit allocation: TBD.  
**Perform/approve:** payment maker, approver, threshold, self-approval: TBD.  
**Recognition:** liability reduction on payment/settlement event: TBD.  
**Posting/close date:** payment business date: TBD.  
**Cancellation/reversal:** linked payment reversal; paid-return handling: TBD.  
**Reporting/settlement:** supplier allocation, remittance, bank/cash match: TBD.

### D09 — Purchase return / receipt reversal
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** reverse D07 inventory/payable; price variance account: TBD.  
**Perform/approve:** inventory actor and finance approver: TBD.  
**Recognition:** dispatch, supplier acceptance, or credit-note date: TBD.  
**Posting/close date:** open return business date; original-period policy: TBD.  
**Cancellation/reversal:** linked to receipt; consumed-stock exception is blocked.  
**Reporting/settlement:** stock return, supplier credit, payable allocation: TBD.

### D10 — Inventory adjustment, waste, or production consumption
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** inventory asset and waste/COGS/variance/production accounts
by reason and unit: TBD.  
**Perform/approve:** counter, recorder, tolerance-based approver: TBD.  
**Recognition:** count approval, waste event, or production completion: TBD.  
**Posting/close date:** movement/count business date: TBD.  
**Cancellation/reversal:** opposite stock and value movement; negative stock policy: TBD.  
**Reporting/settlement:** quantity/value variance and recipe/production reconciliation: TBD.

### D11 — Owner contribution or owner-paid purchase
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** candidate `clearing:owner_paid` versus owner equity/loan;
legal owner and unit allocation: TBD.  
**Perform/approve:** recorder, owner confirmation, finance approval: TBD.  
**Recognition:** contribution, reimbursable liability, or settlement timing: TBD.  
**Posting/close date:** funding/payment business date: TBD.  
**Cancellation/reversal:** linked reversal; reimbursement treatment: TBD.  
**Reporting/settlement:** owner statement, reimbursement and equity/loan reporting: TBD.

### D12 — Cash correction, deposit, or withdrawal
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** cash account plus bank, owner, external clearing, or
variance account selected by typed reason; mappings: TBD.  
**Perform/approve:** cashier proposal and independent approver/limits: TBD.  
**Recognition:** physical custody change or bank confirmation: TBD.  
**Posting/close date:** cash movement business date: TBD.  
**Cancellation/reversal:** no deletion; linked reversal with reason: TBD.  
**Reporting/settlement:** cash log, evidence, close variance, deposit matching: TBD.

### D13 — Transfer between financial accounts
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** explicit active source and destination; cross-unit due-to/
due-from or clearing accounts: TBD.  
**Perform/approve:** finance actor; independent approval threshold: TBD.  
**Recognition:** dispatch, receipt, or bank confirmation for in-transit transfers: TBD.  
**Posting/close date:** transfer business date; two-date transfers: TBD.  
**Cancellation/reversal:** draft cancel or linked opposite transfer; no edit/delete.  
**Reporting/settlement:** both legs, in-transit aging, cash/bank reconciliation: TBD.

### D14 — Account/day close and reopen
**Status/confirmation:** UNCONFIRMED — TBD.  
**Accounts and scope:** each active closable account and business unit; close set: TBD.  
**Perform/approve:** counter/closer, approver, and reopen permission: TBD.  
**Recognition:** closing recognizes nothing by itself; variance-account treatment: TBD.  
**Posting/close date:** account `business_date`; timezone/cut-off: TBD.  
**Cancellation/reversal:** close is reopened, never deleted; reason and approval: TBD.  
**Reporting/settlement:** immutable snapshot, counted/expected variance, sign-off,
reconciliation, and late-posting report: TBD.

## Conversion register

| Artifact | Confirmed conversion | Current safe state |
|---|---|---|
| Account seed data | None | Candidate accounts are seeded inactive. |
| Posting rules | None | Purchase account resolution now rejects missing, inactive, or wrong-type accounts. |
| Permissions | None | Existing permissions are not represented as owner confirmation. |
| Acceptance-test fixtures | Blocking fixture added | It proves ambiguous purchase posting cannot activate/create an account. |

After confirmation, each implementation change must cite decision IDs in its
migration/rule/test, activate only the approved codes and scopes, and add both a
successful fixture and rejection fixtures for wrong unit, role, date, state, and
account. Partial confirmation must not weaken the remaining blocks.
