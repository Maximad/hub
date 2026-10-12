# Hub Suite 3.1 — Phase 4c / D03 customer payment reversals

Status: merged into `main` in PR #265 (`0dc7312`) on 2026-10-12. The
PostgreSQL CI run passed 886 Django tests. **Production deployment has not
been independently verified** and is not part of this follow-up.

## Why this is a standalone finance milestone

The previous `order_payments.reverse` marked the Payment reversed and created a
refund cash movement, but did **not** create an opposing posted ledger journal.
It did not satisfy confirmed D03 in `docs/finance/domain-decisions.md`.

The corrected operation runs through the canonical `dispatch` idempotent posting
engine inside a database transaction and **never** edits the original
`order_payment.collect` PostingBatch or its ledger entries.

## Invariants

1. Finance-only actor and permitted approver. The view also requires the
   `finance` capability and an explicit staff confirmation. Ordinary cashiers
   cannot execute this operation.
2. The refund must **already have been performed** in the original payment
   channel. A staff user must record evidence: a cash handover reference or
   external transfer identifier. The app does not move bank money itself.
3. Each payment must have precisely one **verified original posted**
   `order_payment.collect` journal with two equal source legs:
   cashbox/clearing debit and revenue credit, exactly matching the payment's
   tender and original amount. Legacy payments without a compatible posting
   are **blocked**, not guessed/backfilled.
4. Both original accounts must remain active and in the expected SYP currency.
   On the **new, open business date**, the operation requires an open posting
   period for both accounts. The historical collection date is unchanged.
5. A new `order_payment.reverse` PostingBatch is posted with the original
   credits and debits swapped, then atomically linked by `reversal_of`.
   Original rows remain posted and immutable. D03 uniqueness constraints
   prevent a second reversal journal for the same original.
6. Cash payments record precisely one generated OUT cash movement paired to
   the verified original IN projection. Noncash transfers record no fabricated
   cash movement; their external refund reference is captured as evidence.
7. The Payment's active/reversal flags change only within the same atomic
   operation as the journal and cash projection. Existing Internet/membership
   reversal lifecycle signals run within that transaction and can roll it back
   on failure.
8. PostingCommand idempotency plus payment row locking prevents duplicate
   journals on retries or concurrent requests. The staff form UUID is bound
   to the payment ID in the posting key.
9. `ActivityLog` records actor, permitted approver, reason/category, payment,
   original journal, new journal, and refund evidence. The new journal metadata
   also contains the original journal and tender link.

## User experience

From a visit's existing cashier payment drawer, a finance-authorized staff
member sees a link on each active collected payment, leading to a deliberate
**full-page D03 confirmation**. This is intentionally not a quick one-tap
drawer action. The screen displays original amount and tender, requires a
reason, a refund/handover reference and explicit confirmation that the money
was already returned. Non-finance cashiers do not see these links.

No data migrations, customer QR changes, internet/router changes or changes
to ordinary POS collection were introduced.

## Restrictions

- No partial reversal of one payment. Reverse the complete payment using
  the approved journal workflow. Reinstating a corrected collection is a
  separate, recorded new collection.
- No unverified historical `Payment` can be reversed automatically.
- No actual cash disbursement or electronic transfer is executed by the app;
  it records the completed financial event.
- No reversal on a closed business date or against inactive accounts.
- Existing D05/D06/D10 unconfirmed flows are untouched.
- This milestone does not claim approval of all live-refund policy variants.
  Confirm operational refund handling with the finance lead before enabling
  this workflow for ordinary staff.

## Validation before deployment

The merged 2026-10-12 Django/PostgreSQL CI passed 886 tests. The follow-up
regression suite additionally exercises partial collections, downstream
lifecycle rollback, inactive accounts, malformed original journals,
reversals on a new open day, and CSRF rejection. Run full CI for the
follow-up before merging. Existing baseline coverage includes:
balanced opposite postings, original immutability, cash vs clearing,
idempotent repeat, closed-day rejection, cashier role denial, unposted
legacy payment rejection, missing original cash evidence, invalid reason,
and explicit staff confirmation.

On production, do not perform a real refund as a UI smoke test. Use the
finance team's approved controlled test procedure or a non-production
fixture; verify resulting original and reversal journal IDs and account
day close after any authorized real refund.
