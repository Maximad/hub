# Hub Sweida: colorful staff Operations UI pilot (27 October 2026)

Status: code proposal in a single review PR. Not deployed; not a full staff-device acceptance.

Reference: user-selected Hub Sweida brand-board palette and approved mockups for mobile/tablet Operations.

## Visual and navigation changes
- Use the real Hub brand colors and IBM Plex Sans Arabic: teal #18B7A4, green #25C98D, orange #F77732, purple #A153D1, yellow #FFC929, blue #338CEB, ink #0F2D2E and warm cream #F6F1E8.
- Promote already-authorized new order, kitchen, cashier, internet and close-day routes into touch-friendly tiles.
- Display the existing server-calculated guest Wi-Fi day PIN in a blue internet card, with copy control and manual-copy fallback. Do not hardcode the illustrative PIN from mockups; continue the no-store staff response.
- Existing prioritized staff attention remains; authorized pending fast-internet approvals link to the existing audited handlers.
- Move the previous "today", secondary work directory, and contextual help underneath a **collapsed, near-top More panel**, rather than forcing users to scroll below live work. Preserve existing anchor ids and permission-gated links for navigation compatibility.
- Style operational KPI cards and order cards according to semantic status without changing payment or preparation state machines.
- Screen-reader live status for PIN copy, visible focus, reduced motion, responsive 320–1440px.

## Deliberately unchanged
- No new one-tap whole-order lifecycle writes; kitchen item-level transitions remain authoritative.
- No corrections, cancellation, or disposal of previously open orders or debts. The current 04:00 carryover and money views remain readable.
- No new database migrations, endpoints, prices, billing rules, MikroTik/RouterOS changes, or outside-network requests.
- All routes remain guarded by existing capabilities.

## Acceptance before merge / deploy
1. Verify full Django/PostgreSQL CI and mobile layout CI.
2. Manual RTL screenshots at 320px, 390px, 768px, 1024px and desktop; ensure no overflow and accessible touch targets.
3. Log in as waiter, kitchen/bar, cashier, owner and view-as; confirm that no unauthorized tiles/Internet actions appear.
4. Verify guest PIN when code policy is enabled, disabled and board reminder is due. Test copy HTTPS and manual copy fallback.
5. Test the More anchor navigation from staff header/bottom nav, account drawer links, inbox approvals, search, filters, POS/kitchen/cashier paths, 04:00 handovers, and accessibility.
6. Confirm production revision independently before releasing. Release through normal staging, backup and operational authorization. Do not adjust MikroTik.
