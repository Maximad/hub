# Visit account payment recovery

Based on main `05eadb1`. Internet session recovery PR #337 remains a separate change; this package does not require it.

## Completed

- Both original full-page account forms now send the unique collection/settlement keys already generated for the payment drawer. Fresh forms permit separate equal-value payments. Retrying an original form identifies its original collection.
- Manual payment, order allocations, cash/ledger postings, posting receipts, currency snapshot and partial-approval audit now commit together. A late currency/audit failure rolls all of them back.
- The account row is locked before checking the current balance and partial-payment approval. Concurrent full collections cannot both consume the same balance. The original posting engine and allocation service remain authoritative.
- Completed payment retries are recognized before rechecking the current balance, rates or manager password. They create no extra collection, currency snapshot or approval audit. Current cashier permission is still required. New receipt metadata binds the submitted intent to the original actor. Service receipts also verify the original account and payment amount/method/notes, including a globally colliding key won by a different account.
- No existing receipts, rates, payments or historical data are rewritten. Legacy receipts without the new intent metadata retain source/actor checks and are acknowledged without modifying their historical evidence.
- Failed collection/settlement renders the same page or drawer with an Arabic explanation, refreshed balances/history and the original request key, amount, currency, method and notes. Manager credentials are neither redisplayed nor stored in receipt metadata. A native GET refresh action is available.
- Full-page amounts are filled on the server, so collection can work without JavaScript. Successful actions continue to use the existing redirect/drawer behavior and generate fresh keys for the next collection.
- Settle-and-close retains its existing transaction, finalization, allocation and close services. Unexpected late errors now return the recoverable form instead of an error page. Rolling back closure also restores session state, generated usage bills, credential revocation and audit writes. Denied partial-payment approval still sends the existing manager notifications outside the failed financial transaction.

## Verification

Regression coverage exercises form keys and defaults, safe field retention, permissions/revocation/POST/CSRF, currency/audit rollback, complete-payment replay (including after account closure), changed intent and foreign account/actor rejection, legitimate equal-value collections, manager approval/notifications, late closure rollback and concurrent duplicate/different-key full payments. The existing full Django/PostgreSQL CI is required. Its browser check renders the actual account page at 320/390/768px in RTL with JavaScript disabled and submits the native retry form using isolated preview routes and synthetic data only.

## Risks and rollback

- Replaying a saved request acknowledges the original payment; it never means cash was received again. A new real payment needs a fresh form.
- A legacy receipt has no historical input fingerprint. It can be recognized only from its existing command/source/result and actor evidence. This package does not invent missing historical intent.
- No schema migration, production deployment, RouterOS changes or automatic stock operations are included.
- Revert the PR merge commit to restore the previous cashier handling. Keep existing payment/ledger/snapshot/receipt history. Reverting removes these HTTP recovery and transaction protections.

## Remaining

- Standalone orders retain their existing cashier implementation. Review their late-error and lost-response recovery in a separate package.
- Review account-closure preview and final charge communication with active metered Internet next. This change preserves the existing pricing and settle-and-close policy.
- No financial reconciliation, receipt reversal/refund policy, stock engine, reporting system or new task model is introduced.
