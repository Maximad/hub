# Hub Suite 3.1 — customer single-scan phase

Status: implementation on feature branch; **not deployed**.

## Customer contract

- A physical table QR leads to the **existing** `/menu/table/<qr_token>/`. It does not start Internet, bill a customer, or join someone else's account on GET.
- If the browser already holds a valid credential for **this** table, the same URL is immediately its menu. Otherwise the visitor explicitly creates their own account or joins an existing shared bill using its PIN. This is a **single physical QR scan**, not an automatic or privacy-unsafe joining of shared bills.
- The resulting browser-bound `HubVisit` continues to own table/menu/ordering context; the same visit drives the balance, customer service requests, Internet and device-bound sessions on `/visit/current/`.
- Ordering requires neither membership login nor paid Internet. A single person need not rescan a QR to switch between the menu and visit controls.
- On the menu, linked customers see visible shortcuts to the account and orders; the Internet shortcut appears only when the visit Internet service is actually enabled. It is a **separate navigation flag**, because the legacy catalog decorator deliberately disables the old in-cart Internet storefront.
- `/visit/current/` now always presents the account, checkout QR (when appropriate), assistance, device's active Internet status, and orders. The `?focus=internet` query only **reveals connection options** inside the same page, and keeps balance/orders/service actions present.
- For active unbilled metered Internet, the screen continues to show the ongoing billing warning; a current zero balance does not mean that the session will be free.
- Connection purchase, metered start, entitlement start and stop still use their **existing server-side services** and permission/device checks. The captive welcome path and internal/staff Internet are unchanged.
- Successful Wi-Fi captive relay can continue to return to the original table menu; no second physical QR scan.
- No new client-side cookie/token exposure. Credentials remain HTTP-only where configured. Account join remains PIN/rate limited.

## What changes

- `table_landing.html`: single-scan explanation, explicit account choice preserved.
- `menu.html`: small session-service shortcut navigation whenever a browser-bound visit exists.
- `current_visit.html`: unified headings, anchors, totals and Internet options on same screen.
- `_visit_totals.html`: outstanding balance first, collapsible gross/paid/remaining figures.
- `hub_ui_v3_suite.css`: touch-friendly mobile navigation and account breakdown.
- Targeted tests in `test_table_entry_flow.py`.

## Out of scope / next phases

- Phase 2: staff account-centred drawer actions and quick edits, while retaining URL fallbacks, financial authorization and audited state changes.
- Phase 3: configurable Direct/Team/Event preparation modes without fake intermediate timestamps or weakening role permissions.
- Phase 4: visit/order correction, move/merge/split and financial reversals with explicit confirmation and traceability.
- Phase 5: final information architecture and device-level UX consolidation.
- Media management remains postponed. No new media work or MikroTik configuration.

## Verification

Single full CI run at pull-request checkpoint, plus focused customer tests. Before rollout, manually check a phone QR with no visit, shared visit PIN rejection and valid join, return after refresh, order creation, service request, Internet options and device session, open metered billing warning, and cash settlement QR. Verify on Hub Wi-Fi and mobile data if possible.

Production remains unchanged until merge and an explicit VPS deployment. Existing production backup/restore verification must remain enabled.
