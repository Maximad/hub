# Hub Sweida · visual direction 2026 — implementation 01

Status: **feature branch only — not deployed**. Based on the October 2026 colorful handout and the already operating Hub UI 3.0 / staff UI v2 code. The illustration is a design reference, not a screenshot of production.

## What this branch implements

- New presentation-only stylesheet `static/css/hub_visual_2026.css`, loaded last on the public menu (`/menu/` and table-menu entries) and staff Operations (`/staff/`) only.
- A warm cream/turquoise customer welcome, clearer first two **real** menu-section discovery cards, cleaner search/category rail, food-photo cards and cart surface.
- Existing dynamic product and section media remain source-of-truth. Nothing in this change creates imaginary products, events or prices.
- A calmer Operations presentation: compact accents, more readable account/KPI/order surfaces, existing toolbar and contextual drawer unchanged.
- Scoped Django read-only tests for style entrypoints, contrast pairs and preservation of the existing order/payment/permission hooks.

## Preserved and excluded

- Preserve original configured Hub logo, server-rendered templates, RTL, current product media, all forms, CSRF, links and business logic.
- Keep `#137F70` for primary action text/background contrast. Bright thematic swatches are **not** payment, availability or permission states.
- Leave the staff v2 top application shell and mobile bottom sheet intact. The illustration's left sidebar is not an approved replacement for the existing navigation.
- No QR, database, inventory, price, provider, Internet, router, network, settings or operational data modifications.
- No live site, physical signage, posters or social accounts changed by this branch.

## Release checkpoint

1. Run read-only tests:
   `python manage.py test core.tests.test_hub_visual_2026 core.tests.test_hub_ui_v3_suite core.tests.test_hub_ui_v3_pilot`
2. Run `python manage.py check`, `python manage.py collectstatic --noinput` in a safe environment, and the repository's standard CI.
3. Verify RTL and media at 360, 390, 430, 800 and 1280px. Inspect with 0/1/2+ menu sections, long Arabic names, missing photos, open cart, modifiers, checkout, offline font and keyboard focus.
4. Verify staff Operations account and payment drawers, mobile bottom sheet, read-only View As, data filters, roles, KPIs and HTMX refresh after payment.
5. Check print/receipt output, text contrast on real images and `prefers-reduced-motion`.
6. Compare screenshots with v3 on phone and desktop. Correct verified regressions before approving merge/deploy.

**Deployment remains an explicit later decision.** If approved, follow the existing Hub Suite backup, CI, production deployment and smoke-test runbook. Never make MikroTik changes for this visual update.

## Follow-on work (not claimed completed here)

- Build a genuine customer home/visit landing with navigation for services actually enabled on the server, not hardcoded icon links to hypothetical modules.
- Refine product details / cart and staff preparation by measured device screenshots, avoiding another global CSS patch stack.
- Produce editable Hub social masters (story, feed, event, food, weekly agenda) and document the Masharib/Sufra sub-brand rules.
- Inventory and measure physical wayfinding and print signage before fabrication.
