# Staff UI v2 visual review — 2026-09-03

Device review after the first production deployment of PR #218 identified presentation issues only; no domain behavior required changes.

## Corrections in this pass

- Move staff notifications from page content into the application header.
- Keep notification controls compact, with a bell/badge treatment and mobile brand + bell header.
- Remove the large secondary page-space cost caused by notifications.
- Force the desktop navigation surface to remain transparent inside one thin application bar.
- Reduce Operations hero, toolbar and KPI vertical density.
- Remove the redundant `عرض` affordance from KPI cards.
- Make mobile KPIs a horizontal scroll rail rather than two large rows.
- Reduce mobile title/action/search spacing so accounts appear much earlier.
- Correct inactive/disabled action colors so labels remain legible.
- Remove the nested scroll behavior from the active-orders side panel.
- Preserve account-card and bottom-navigation patterns from the accepted first pass.

## Scope

Visual/UI only. No migrations and no changes to order, visit, payment, Internet, inventory, finance, or RouterOS behavior.
