# Hub Suite — Charcoal POS (Phase 2)

**Branch:** `feat/charcoal-pos-workspace`  
**Parent:** `feat/charcoal-creative-control-room` (PR #403, pending CI).  
**Status:** design/implementation candidate; **not production tested or deployed**.

## Scope

Reuses `static/css/staff_charcoal.css` and its existing dark tokens. `templates/staff/pos.html` opts into `staff-charcoal-pos`. No second theme engine, no altered Django views, permissions, POS forms, product catalog, pricing, modifiers or JS.

The POS remains deliberately efficient: same fast-touch product grid, quantity controls, order context, checkout/cart position and mobile responsive presets. Changes are dark layered surfaces, improved card and text contrast, teal primary action, subdued amber editorial header and accessible focus.

## Verification

- `python manage.py test core.tests.test_staff_charcoal core.tests.test_staff_charcoal_pos core.tests.test_staff_design_system core.tests.test_staff_operations_attention`
- Full Django/PostgreSQL CI; static collection.
- Visually verify staff POS in RTL at 360/390/430/800/1280/1440px, including mobile cart and horizontal category navigation.
- Test a real draft order with fulfillment modes, modifiers, quantities, product availability, membership lookup, table binding, required fields and cart recalculation.
- Validate no incorrect orders or payments on errors; confirm cashier and kitchen handoff.
- Check disabled states, sticky cart overlap, contrast, low bandwidth, reduced motion and print.
- Do not merge/deploy until both PRs independently pass review and relevant browser QA.

## Release sequence

1. Clear and merge #403 after CI / user approval gates.
2. Rebase or retarget this stacked PR onto `main` with only POS-scoped files.
3. Confirm full suite CI and manual operator smoke tests on the merged baseline.
4. Merge POS separately; do not assume GitHub merge equals production deployment.
