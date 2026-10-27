# Hub Suite — Staff Operations full visual redesign (28 October 2026)

Status: review implementation, not deployed.

This redesign implements the visual structure of the founder-selected colorful mobile/tablet dashboard rather than recoloring the prior long page. Hub brand palette and IBM Plex Sans Arabic are reused. Staff workflows, authority, financial records, daily PIN policy, fast-internet decisions and RouterOS remain unchanged.

## New structure
1. Compact title and prior business-day notice with original manual-reconciliation workflow preserved.
2. Prominent dynamic daily guest Wi-Fi code/copy and Internet management section; existing permission gate retained.
3. Four colorful mobile actions (new order, kitchen, cashier, close-day), five on larger devices including Internet.
4. Two-by-two mobile KPI tiles: current business-day active/ready counts, current open visits, historical visits. Existing overall counts remain in full-order views.
5. Inbox for existing auditable tasks and pending fast Internet approvals.
6. Search, filters and section tabs in an optional expandable group.
7. Dedicated active-today cards first, with separate collapsed carried-over orders; item-level preparation and payment links still go through authoritative routes.
8. Historical collection and operational detail preserved inside expandable sections; existing account cards remain accessible. Role-gated More tools preserved.

## Safety and acceptance
- New summary counts are read-only projections using configured business-day boundary and order created_at; no records modified.
- Query prioritizes today's orders before old handovers within the existing 20-item viewport, then retains the existing ready-first sorting.
- No fictitious 'start/ready/served' POST operations, no fabricated Wi-Fi health metrics, and no hardcoded PINs from mockups.
- Verify Django/PostgreSQL CI and role permissions; test around 04:00 cutoff, 22+ orders with old carryover, paid/served exclusions, PIN/copy, old-day close alert and browser drawer links.
- Manually check 320/390/768/1024px and desktop. The generated mockup is a visual reference, not proof of pixel-identical production rendering.
