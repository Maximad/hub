# Hub Suite charcoal management overviews

**Scope:** authenticated Hub Sweida Finance, Inventory and Reports **overview** pages only. No grant, Habaq editorial or other entity books are joined into Hub data.

## Implementation
- Use the approved charcoal/graphite/teal/amber design tokens from the single existing `static/css/staff_charcoal.css`.
- Opt in `finance_home.html`, `inventory_home.html` and `reports_home.html` with the class `staff-charcoal-management`.
- Existing role-aware module navigation, active tab context, key performance indicators and caution notes remain visible.
- Numeric values remain server-rendered; no computed figures or new aggregations are introduced.
- Non-overview finance/inventory forms and Django admin retain current styling until separately verified.

## Protected operations
No modification to database schema, financial transfers, receipts, accounting periods, cash collection, inventory movements, purchases, payment reversals, discounts, approval policies, report generation, or user permissions. In particular, finance values are the current **Hub Sweida** system values, not consolidated Habaq accounts.

## Before merge
1. `python manage.py test core.tests.test_staff_charcoal_management core.tests.test_staff_charcoal_cashier core.tests.test_staff_design_system`
2. Full PostgreSQL CI including collectstatic.
3. Screenshot and keyboard verification at 360, 390, 768 and 1440 px in Arabic RTL; validate overflow on long numeric labels.
4. Verify expected vs actual cash, business date, low stock and missing cost rows against unchanged values.
5. Confirm finance / inventory transaction forms remain unmodified.
6. Check readable contrast and ink-efficient print formatting.
7. Verify actual deployed production revision separately; merge does not mean rollout.
