# Hub Suite 3.1 — Phase 5: unified staff workspaces

## Deployment status and release boundary

Development-only change. Do not assume production reflects this branch.
Merge to main only after Django/PostgreSQL CI succeeds. Production deployment is
a separate one-time operator action after all approved work is merged, backed
up, and restore-verified.

No database migrations, schema changes, payment/stock posting changes, router
changes or replacement of the existing full-page workflows.

## Repository audit: existing routes and page families

| Domain | Existing primary routes and presentation | Phase 5 action |
| --- | --- | --- |
| Operations | /staff/, /staff/orders/, /staff/visits/, /staff/pos/, /staff/cashier/ | Keep unified floor and contextual order/visit/payment drawers. Group destination links into اليوم and الإدارة; normalize order/visit headers and actions. |
| Kitchen and bar | /staff/kitchen/, /staff/prep/ | Preserve configurable Direct/Team/Event transitions; direct navigation from shell stays role-gated. |
| Finance | /staff/finance/, expenses, daily close, purchases, cash movements, transfers | Reduce always-visible finance navigation to daily destinations, retain advanced routes under a native details element; rebuild overview as readable KPIs and reconciliation links. |
| Inventory | /staff/inventory/, items, purchases, movements, low stock, recipes, reports, prep batches | Show core daily actions first; leave specialized reports and recipes one disclosure away; connect KPI cards to actual existing routes. |
| Reports | /staff/reports/ and day/margin reports | Use manager-oriented summary and next links without implying that viewing a report posts money. |
| Events and reservations | /staff/events/, /staff/reservations/ | Events index now uses compact event cards; retain reservation check-in/visit flow and event detail editor as full-page fallbacks. |
| Members and internet | /staff/members/, /staff/internet/, /provider/ | Improve member search/actions and group homepage links. Preserve customer Internet and provider portal boundaries; no RouterOS work. |
| Catalog and admin | menu tools, modifiers, QR, imports, users, vendors and Food Lab | Keep advanced tasks in the management directory, visible only under suitable capabilities. |
| Notifications | staff notification bell and /staff/notifications/ | Preserve working notification, preference and polling semantics. |
| Customers | public menu, table QR, current visit | Not altered; existing unified table QR flow stays intact. |

## Information architecture

- **العمليات / Now:** existing live visit cards, pending order list, POS and
  cashier drawers. This page remains the staff landing space.
- **اليوم / Today:** tasks, close-day summary, reservations, events,
  Internet sessions, full-order and full-visit views.
- **الإدارة / Manage:** finance, inventory, reports, members, catalog,
  imports, QR, users, vendors, and specialty tools as permitted.
- On module pages, primary actions are visible immediately while advanced
  tools stay one disclosure away. These links remain ordinary accessible
  links: Javascript is not required to access any route.

## Safety and usability contracts

1. No new posting path, signal bypass or financial write in these changes.
2. Render links according to existing effective staff capabilities. Server-side
   decorators/services still enforce authorization.
3. Keep existing context drawers, htmx refresh loops, full-page forms,
   printable receipts and fallback links.
4. Arabic RTL, keyboard focus, skip-to-content, 44px mobile touch targets,
   local layout CSS and reduced-motion support.
5. No invented KPIs, cash differences or event schedules: existing backend
   aggregations and records determine all displayed values.
6. A linked KPI opens its established source view; it is not a mutation.
7. Production features are not considered verified until the deployed commit,
   health checks, backup evidence and an authorized non-destructive smoke test
   agree.

## Acceptance checks

- Authenticated admin can reach each existing module and group links from
  the homepage; anonymous users cannot access the staff area.
- Waiter sees relevant daily routes without manager-only destinations.
- Finance and inventory primary and advanced links render and resolve.
- Events, members, visits, orders and reporting render with existing context.
- No duplicate top-level main landmark; public and provider interfaces retain
  their existing shells.
- Django check, migration drift check, collectstatic and all PostgreSQL tests pass.
- New UX tests live in core/tests/test_phase5_staff_navigation.py.

## Manual pilot at the Hub, after authorized deployment

Test on one small phone, one cashier tablet, and desktop with relevant roles:
place an unpaid order, open the account drawer, mark a prep action, review the
cashier panel without reversing or collecting test money, search a member,
review an inventory shortage, and inspect a day report. Confirm keyboard and
RTL navigation, advanced disclosure links and print fallbacks. Do not create
real financial refunds as a smoke test.

## Intentionally not claimed as complete

Complex event staffing, automatic preparation scheduling, media card uploads,
physical stock valuation redesign, live notification performance telemetry,
and new provider/RouterOS behavior are separate product decisions. Advanced
finance concurrency and crash-restart tests are separate from visual UX work.
These should not be silently bundled into an interface-only release.
