# Purchase receipt recovery — 21 October 2026

Base: main `3405cfc` (includes PR #334). Scope: the existing Inventory purchase receipt action and Finance purchase detail action. No new model, migration, task engine or operational policy.

## Findings and completed changes

- Both receiving screens lost entered partial quantities and selected units when a validation error redirected to a fresh page. They now render the original purchase detail page with inline Arabic errors, submitted values, current received/remaining quantities and the same attempt token. Missing rows default to zero on correction, so an omitted row does not silently become a full delivery.
- A quantity maximum is rendered in the selected entry unit, including after an error. The form works without JavaScript; the existing unit helper still converts display values when units change.
- Content-derived request keys could suppress a second legitimate partial delivery with identical quantities. Each newly rendered receiving form now has a UUID attempt token. The existing posting context is scoped to purchase and actor, independent of which of the two receiving screens handles a retry. Reposting that token returns the existing receipt; loading a fresh form represents a new delivery.
- Malformed tokens fail before posting, preserve quantities, and receive a replacement token for correction. Unknown lines and incompatible units remain server-validated. No arbitrary package conversion was introduced.
- Unexpected receiving failures are logged server-side with the purchase identifier and return a generic Arabic retry message. Exception details are not copied into the staff page. Existing validation messages remain inline.
- The original `purchase_posting.receive` transaction remains responsible for locks, remaining-quantity checks, receipt lines, stock movements, supplier liability and rollback. Receipt confirmation does not pay the supplier.
- Existing capability checks and the Inventory cashier/admin restriction still run before every POST, including duplicate retries. No new permission or role was added.

## Verification

`core/tests/test_purchase_receipt_recovery.py` adds 12 PostgreSQL integration tests covering value/unit retention, invalid input, omitted rows, malformed tokens, equal partial deliveries, retries across both screens, purchase token scoping, revoked permissions, read-only users, cancellation/GET safety, rollback after the first line and rollback after stock application followed by successful retry. Existing unit conversion tests now expect the inline error response.

`scripts/check_purchase_receipt_mobile.py` renders the actual two purchase detail templates using unsaved synthetic objects. Existing CI checks RTL, viewport bounds and retained quantity/unit/maximum at 320, 390 and 768 px, with and without JavaScript, and uploads screenshots in its existing mobile preview artifact. No production fixtures or manual staff trial are required.

Local Django system check and migration drift check pass (the isolated PostgreSQL database is provided by CI). Full Django/PostgreSQL and browser results are recorded in the PR after CI completes.

## Risks and rollback

- A freshly loaded form has a new token and represents a new delivery. Staff must retry the same submitted form when recovering a request; remaining quantities are rechecked under the existing purchase lock. The form cannot infer whether two independently entered deliveries represent the same real-world goods.
- Legacy callers that omit the new token keep their existing request-key behavior, including their existing content-based retry semantics. No global posting engine behavior changes.
- Rollback: revert this code change. There is no migration or data backfill to reverse. Already posted receipts, stock movements and liability entries remain canonical and must use existing authorized reversal operations if an operational correction is needed.

## Remaining work

- Review Internet session activation/retry errors against existing network operation evidence; do not change RouterOS configuration.
- Continue staff sales/payment error-recovery review separately.
- Universal applied/reversed stock evidence remains separate work; no historical movement inference was added here.
- Deployment and production SHA verification remain separately authorized work. No deployment or production database access was performed by this package.
