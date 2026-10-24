# Staff Operations attention-first pilot — 25 October 2026

Status: implemented in draft PR #346, pending full PostgreSQL CI and device acceptance. No production deployment.

## Approved P0 behavior

The staff `/staff/` landing page foregrounds actionable work, not a directory of operations:

- `new`, `accepted`, `preparing`, and `ready` orders still need service or handoff regardless of payment status.
- A paid order with outstanding service remains visible as a service task. A paid and served order does **not** remain a live task. A served standalone order with a remaining charge appears in a separate collection section.
- Live orders are prioritized ready-to-handoff first, then new, accepted and preparing, with oldest in each class first. KPI counts cover the complete matching queryset, before the 20-card limit.
- Ordinary item editing is unavailable after any real collected payment or a classified hospitality/owner consumption. Staff may view details; any accounting correction requires its existing authorized path. The server enforces editing restrictions even with an older browser.
- Each active order card offers only routes allowed by existing capabilities. Links look clickable and use **تعديل** only if edit policy permits; **التفاصيل** otherwise.
- The four formerly dominant daily actions are replaced by one native expandable floating shortcut. Roles and server permissions remain unchanged, existing mobile bottom navigation stays intact, and occasional work is in the existing footer/more areas.
- The daily guest Wi-Fi PIN is in a compact collapsed Today's section with a pending-board-update indicator. Staff Operations response keeps `Cache-Control: private, no-store`.
- Existing business-day close, financial `DailyClose`, internet lifecycle, and router configuration are unchanged.

## Verification gates

1. Full Django/PostgreSQL CI and no new migration.
2. Mobile visual checks at 320, 390, and 768px: floating menu is not obscured by bottom nav, task card actions remain tappable, and the collapsed PIN is legible when opened.
3. Staff roles: cashier, admin, waiter, kitchen; a denied permission must not leak purchase/stock/payment actions.
4. Paid-ready still appears for handoff and is details-only; served-paid disappears; served-unpaid standalone is collectible; partly collected order cannot change its items.
5. Confirm a pre-existing account/visit payment drawer, quick edit on eligible orders, order details, role menu, keyboard navigation and failed-network fallback work.
6. Verify production revision before claiming this is live; deploy only after backup/restore verification and authorization.

Later sprints: approval-request expiry/history, creator/beneficiary attribution, and neighbor/staff discount policies. These are out of scope, not implicitly approved.
