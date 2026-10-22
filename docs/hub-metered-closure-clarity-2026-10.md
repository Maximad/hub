# Final metered Internet charge communication (cashier visit closure)

This bounded change improves cashier disclosure while retaining the existing visit settlement and Internet billing services. The balance shown before closing is the **already-recorded account balance**. It does not include possible charges from running metered Internet sessions. The exact charge is determined when those sessions are stopped as part of settle-and-close.

## Behavior

- Full cashier visit and contextual payment drawer distinguish active metered sessions from other active Internet sessions; warn that the visible recorded balance is not the final payable total.
- The warning asks staff to explain the potential additional charge before completing the existing action. It does **not** offer a guessed quote, stop a session early, or introduce an approval/payment policy.
- Closure still runs atomically: stop sessions, post any billed Internet order, compute actual remaining account balance, collect that amount, close visit.
- The success message gives the **actual money collected in the closing transaction**, not the full lifetime visit total. If newly billed Internet increases the due, it also states the newly added amount.
- The existing closure ActivityLog includes `recorded_due_before_finalization_syp` and `new_metered_due_syp` alongside the original `amount_syp`, without changing any accounting or valuation rule.
- A read-only GET never finalizes or creates an Internet charge. RTL mobile/no-JavaScript preview covers the warning.

## Boundaries

No price-policy changes, unverified current-time quote, session/network operations outside the existing settlement, router changes, database schema changes, production-data changes or deployment. Existing #337–#339 recovery rules and finance permissions remain intact.

## Tests

Django/PostgreSQL tests verify full page + drawer disclosure, zero-mutation GET, ordinary non-metered closure, final collected amount with newly billed Internet, and audit evidence. Run full CI before merge. Real staff/device acceptance remains a separate pending requirement.
