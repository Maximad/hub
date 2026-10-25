# Sprint 6 — Business Day rollover without lost handovers or liabilities

**Date:** 25 October 2026
**Status:** development PR under CI; VPS not deployed.

## Existing source of truth

Business Day cutoff is already configured in Django (`BUSINESS_DAY_CUTOFF_HOUR`, default 04:00 Asia/Damascus). `BusinessDay` owns day open/closing/closed/reopened states, immutable close snapshot and staff tasks. `Order`, `Payment`, `HubVisit`, and `InternetSession` own operational/financial balances and handovers. Do **not** reset balances, finish unpaid orders, waive tasks, or auto-close a prior day just because the clock crossed 04:00.

## What Sprint 6 changes

1. **Current task attention is day-scoped**: the Operations task feed shows *pending actionable tasks for the current business date*, only if that day has open/closing/reopened state. A prior Business Day may remain OPEN until a manager reconciles it, but its historical unfinished tasks do not reappear as today's work. Existing task history remains and can still be reviewed on the task console.
2. **Prior open-day warning**: Operations visibly tells staff when an older business day is still open and links the existing `staff_close_day` workflow. This is informative, not an automatic mutation, authorization change, or claim the new day has been opened.
3. **Outstanding fulfillment continues across days**: active `NEW`, `ACCEPTED`, `PREPARING`, and `READY` orders remain in the staff handoff queue whether they were created before or after cutoff, provided their visit is still open (or they are standalone). Older rows receive a “من يوم عمل سابق” label and a separate carryover count. A **paid but not served** order remains actionable; served/cancelled and closed-visit orders do not.
4. **Old visit-account debts**: existing stale-visit review remains unchanged; it is not merged into current-day collection metrics.
5. **Old standalone unpaid orders**: show older `SERVED` standalone orders with unpaid balances in a separate, cashier-only collection review. Do not mark them as new sales or mix them into today's unpaid count. To protect page latency, scan at most the most recent 500 historical served standalone candidates, show up to 10 unpaid, and link the complete Orders screen when the scan is capped or more unpaid exist. Historical liabilities remain in the original order and payment tables regardless of whether Operations can display all of them.
6. No changes to financial posting, payment reconciliation, kitchen prep transitions, Internet sessions, daily code rotation, task writes, or MikroTik configuration.

## Tests & acceptance

- PostgreSQL CI for cross-day paid READY orders, standalone READY handovers, settled orders, closed visits, late old visit debt, old served standalone liability, role-scoped read-only task feed, and exact 03:59 vs 04:00 task selection.
- Verify current-day task display after the *authorized* previous-day closing and next-day opening. No silent carryforward or task deletion.
- Test at 320/390px staff screens: carryover badge, past-day warning, collection link, permissions and correct ordering. Verify no new order/payment/ActivityLog writes on an ordinary GET.
- Check production deployed revision and database backup/restore evidence before normal authorized release.

## Deliberately deferred

- Automatic rewriting or reassignment of unfinished previous-day tasks.
- Automatic completion of unpaid orders or old visits; no retroactive payment.
- Additional finance-policy or staff-benefit mechanisms.
- A full cross-period receivables/reconciliation dashboard (existing finance system remains the source of truth).
