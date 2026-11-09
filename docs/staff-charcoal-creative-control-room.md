# Hub Suite — Charcoal Creative Control Room (Step 1)

**Scope:** charcoal header/navigation across authenticated staff pages; full dark surfaces exclusively on `/staff/` Operations (`staff-charcoal-operations`).  
**Status:** feature branch, pending PR review/CI/manual browser QA. Not merged or deployed.

## Design contract

**Expression on arrival; precision in action.** The staff workspace takes the approved prototype's architectural/kinetic editorial identity without duplicating its fictional data or replacing the live Django/HTMX product.

Palette: canvas `#15181C`, surface `#1E2329`, raised surface `#262C33`, line `#343C45`, primary text `#F3F1EC`, secondary `#A9B0B8`, action teal `#24C6B2`, sparse amber `#D88A3D`. Semantic warnings, errors and payment/order status remain distinguishable by **text and context**, not branded accent alone.

### Source of truth for this theme

- `static/css/staff_charcoal.css`: **one** new, scoped token and visual layer, loaded after the existing v2/v3/2026 staff CSS; do **not** duplicate dark overrides into `staff_foundation.css`, `staff_ui_v2.css` or `staff_operations_designed.css`. The shared staff header uses charcoal design tokens; full dark surfaces and bridges to `--staff-v2-*` / `--hub-*` are **opted into only by Operations** (`staff-charcoal-operations`) to avoid untested POS, kitchen and finance contrast regressions.
- `templates/base.html`: guarded dark CSS and Focus JS entrypoints for logged-in `/staff/` pages only; staff PWA theme color updated. Public menu, Wi-Fi, provider portal and Django admin are unaffected.
- `templates/includes/staff_shell_nav.html`: a single `type=button` Focus toggle, alongside the existing role-gated navigation and notification tools.
- `static/js/staff_charcoal.js`: local, client-only preference. No AJAX, database changes, cookies, session mutations or backend API. Degrades gracefully when localStorage is disabled.
- `core/tests/test_staff_charcoal.py`: source-level regression contract (not a substitute for rendered-browser or PostgreSQL tests).

**Why the old CSS is still present:** removing it before the full suite migration would risk POS, kitchen, drawers, low-bandwidth assets and specialist pages. This PR adds **one** explicit compatibility layer; consolidation/removal of proven redundant styles belongs in later visual-module PRs, with screenshot and regression evidence. Avoid adding another CSS layer for the next module: evolve this file and migrate old selectors only when safe.

## Focus mode

Use the `◎ وضع التركيز` button in the staff header. The button exposes `aria-pressed`, remembers a browser-local preference, and only removes Operations hero artwork and optional transitions. No operational information, actions, alerts, forms or validation are hidden. The default is the fully art-directed mode.

## Protected boundaries

- No route, model, database, role/permission, order, payment, booking, event publication or inventory change.
- No changes to the existing Internet worker or MikroTik.
- No public menu, QR/cart/checkout, admin, provider, customer account, receipts or print workflows.
- Existing Operations filters, account/order cards, Wi-Fi PIN copy, dialog/drawer and responsive tabs are retained.

## Verification

1. Run `python manage.py check`.
2. Run `python manage.py test core.tests.test_staff_charcoal core.tests.test_staff_operations_attention core.tests.test_staff_design_system core.tests.test_hub_ui_v3_suite core.tests.test_hub_visual_2026` against the project test database.
3. Run the repository's PostgreSQL CI and `collectstatic --noinput` in a safe environment.
4. Browser-check `/staff/` on 360, 390, 430, 800, 1280 and 1440px: Arabic RTL, scrolling, Focus toggle, live roles, keyboard navigation, notification dropdown, drawers, Wi-Fi PIN, filters and print.
5. Check POS/kitchen/cashier/internet routes at least for readable navigation and functional role gates, as they share the global shell. Check high contrast on forms, loading and empty states.
6. Test low-bandwidth optional font fallback and `prefers-reduced-motion`. Verify focus mode with storage unavailable.
7. Do not merge/deploy until visual/browser evidence and functional regression checks pass.

**Compatibility:** other staff modules keep their existing light content surfaces temporarily. This is a deliberate staged rollout, not a claim of suite-wide dark parity. The charcoal header applies across those modules and needs mobile smoke review.

**Current limitation:** this branch was authored through GitHub source operations. A live Django server, browser screenshots from real application data, PostgreSQL suite, CI and production revision were not independently executed/verified during authoring.

## Next phases (separate reviewable PRs)

1. Events & Reservations — coordinate against recently merged recurrence/performance work; avoid schema and booking changes.
2. POS and preparation — visual density and touch targets, not price/payment logic.
3. Inventory, finance and reports — migration of forms and tables under the existing semantic tokens.
4. Public menu, member account and venue discovery — art direction adapted for customer use, separately authorized and tested.

Production release remains a separate founder decision.
