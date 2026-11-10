# Staff module switcher — navigation usability

**Scope:** staff navigation UI only, Hub Sweida. No permission, route or business-logic changes.

## Problem

The desktop header's "الأقسام" link and mobile "المزيد" link sent staff to `/staff/#staff-more`, requiring a detour through Operations for module discovery.

## Implemented

- Native `<details><summary>` **الأقسام** menu in desktop staff header.
- A **المزيد** mobile sheet positioned above the existing five-item bottom bar.
- One shared `templates/includes/staff_module_links.html` with existing `staff_caps` gates, not two independently maintained link lists.
- Navigation grouped into daily operations, program/audience, and administration/resources, linking to existing routes.
- Existing `staff_account_menu.js` enhancement handles Escape and outside click for both account and module menus. Both controls work without JavaScript through native `details`.
- Added styling to the existing `staff_account_menu.css`, not another global stylesheet.
- Existing account menu, user permissions, mobile five-column navigation, logout POST, finance/editorial boundaries and other functionality unchanged.

## Verification

1. `python manage.py test core.tests.test_staff_module_switcher core.tests.test_staff_account_menu core.tests.test_staff_design_system`
2. Full Django/PostgreSQL CI.
3. Browser checks: desktop 1024/1280/1440px, mobile 320/360/390/430px, RTL, keyboard Enter/Space/Escape, outside click, focus return, scroll limits and display with viewport keyboard.
4. Verify manager/admin vs waiter restricted links and View As state.
5. Ensure module menu overlay does not cover payment forms or dismiss active transaction dialogs.
6. Screen reader announcement of summary and link groups, and scroll on mobile.

Release/production deployment is separate from merging source code. This PR is not a theme-wide deployment.
