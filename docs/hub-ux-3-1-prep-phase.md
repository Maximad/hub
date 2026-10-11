# Hub Suite 3.1 — Phase 3: station preparation workflow profiles

Status: development milestone on a feature branch. **Not deployed.**

## Rationale

One employee currently receives, prepares, and delivers bar items, while large events will need independent preparation, handoff, and service teams. Do not enforce multi-tap handoffs in every normal shift or discard the established transition model.

## Profiles (per active PrepStation)

| Station profile | On the board | Authorization and recording |
| --- | --- | --- |
| **Direct** · مباشر | `تم التقديم` once after physical delivery, rather than clicking accept/prepare/ready/served | Eligible in-place items only. Server checks current item status, non-cancelled order, station configuration and operator permission. A **single real transition** `new/sent/accepted/preparing/ready → served` is audited with `transition_kind=direct_completion`. There are no fake intermediate states or timestamps. |
| **Team** · فريق | Existing steps accept → prepare → ready → served | Exact existing permission/transition structure. **Default for every existing station and new station** to prevent surprise during rollout. |
| **Event** · فعالية | Detailed Team steps, labeled for event operations | Same existing safety model. The event setting is currently an explicit per-station profile selection, **not** an automatic event-scheduling switch or staffing/priority engine. Those are possible later extensions. |

A station can switch profiles without altering any item's historical state. A partially progressed item can use Direct completion if it is actually delivered after switching to Direct mode. Detailed legacy transition requests remain valid to complete an already-started chain. A disabled/missing prep station defaults to Team semantics.

Managers can set modes at `/staff/prep/profiles/` (available from the board) and in Django admin under Prep Stations. Only authenticated owner/admin users may change modes in the staff UI. Each changed profile is recorded in `ActivityLog` including actor, station, old and new profile; redundant submissions do not record false changes.

## Safety

- POST-only transitions, normal CSRF and `kitchen_board` capability checks.
- One-tap Direct action additionally requires an operator authorized to prepare/serve; bartender/kitchen staff can use it only while their station is in Direct profile. Cancelling is still restricted under existing cashier/admin policy.
- Direct action is forbidden for delivery orders, cancelled orders, NO_PREP items, cancelled/served items, and stations not configured for Direct.
- Item state is locked using `select_for_update()` before any permission and transition validation; a replay from a stale device cannot create a second completed transition.
- All item and profile changes are append-only ActivityLog events; OrderItem state stays authoritative. No payment status is implied by serving. The database preserves orders, line items, stock postings, prices, payments and all previous audit history.
- Ready/cancel notifications continue to be issued for the original workflow. A Direct completion does **not** send a misleading intermediate ready notification.
- No MikroTik, Internet/ISP, customer QR, cashier, stock, discounts, member or booking logic is touched.

## Migration and rollout

- Additive nullable-free `catalog.PrepStation.workflow_profile` field, default `team` via catalog migration 0009.
- Existing stations continue operating exactly as before until a manager explicitly selects Direct/Event.
- Deploy only after full Django/PostgreSQL CI. Manual smoke on real staff devices: switch the bar to Direct; complete a new drink; verify order totals unchanged; reject a stale duplicate submission; then switch it back to Team and check separate transition buttons. Confirm kitchen station is unaffected and event mode shows detailed steps.
- To roll back UI usage immediately, set every station profile to Team. No data removal or router changes required.

## Next milestone

Phase 4: expanded order/visit corrections, splitting and transfers, financial reversals, with audited authority and settlement checks. Staffing assignments, queue priorities and scheduled event overrides are **not** claimed as delivered in Phase 3.
