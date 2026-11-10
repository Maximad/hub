# Hub Sweida — Public Charcoal Experience

**Initiative:** Hub Sweida commercial/public interface (not Habaq Media).  
**Owner:** Hub Suite / Hub Sweida operations.  
**Status:** implemented in feature branch, pending CI and live-device visual acceptance.

## Why the homepage did not use charcoal

The homepage and public event/discovery implementation already exist. Earlier charcoal PRs (#403–#408) intentionally scoped their new staff stylesheet to authenticated staff pages. Public homepage styles remained in `public_homepage.css` and customer and Wi-Fi routes kept separate light palettes. This change is the **public counterpart**, not a new homepage implementation.

**IMPORTANT:** `PUBLIC_HOMEPAGE_ENABLED` is untouched. The existing publication flag and eligible, actually published event/program controls are preserved. It must not be toggled automatically as part of a styling release.

## Included surfaces

- Live homepage `/`, public events, programs, pop-ups, membership discovery, attendance and corresponding detail/form pages through the existing `public/base.html`.
- Public menu `/menu/`: live catalog and product artwork, categories, cart review, sticky cart and checkout.
- QR/table number entry, current visit, order confirmation and guest membership/account pages.
- Wi-Fi welcome, Internet choice sheet and staff Wi-Fi login. Both use **local stylesheet assets only** and do not require network fonts or external CSS.
- Transitional MikroTik HotSpot auto-post and invitation acceptance pages receive an equivalent small local, inline charcoal treatment; their forms remain exactly as before.

## Design

Uses the already-approved graphite / teal / amber palette:

- Canvas `#15181C`, surface `#1E2329`, raised `#262C33`, border `#414A52`.
- Primary text `#F3F1EC`, secondary `#A9B0B8`, accessible teal `#24C6B2`, amber `#D88A3D`.
- Retain real venue imagery and event posters. Empty media uses designed typography/graphic fallback; no synthetic event or fabricated commercial offer.
- Single late-loaded `static/css/public_charcoal.css`, scoped to **explicit public body classes**, not staff/provider.
- RTL, reduced-motion, visible focus, touchscreen buttons, responsive typography, print rules.

## Nonnegotiable functional boundaries

No view, router/HotSpot provisioning, order, payment, session, membership entitlement, public event publication, seat capacity, attendance queue, network authentication, idempotency key, CSRF or server-side permission changes.

There are no new sales claims, opening-hour claims, live event announcements, price alterations, redirects, booking links or hidden purchase steps.

## Verification and acceptance

1. Full Django/PostgreSQL CI including `core.tests.test_public_charcoal`, `core.tests.test_public_homepage`, `core.tests.test_hub_ui_v3_suite`, `core.tests.test_wifi_entry` and existing customer-order/account tests.
2. Test `PUBLIC_HOMEPAGE_ENABLED=true` and `false` behavior explicitly. This code cannot enable the homepage by itself.
3. Real-browser screenshots for homepage, /events, menu with product images and empty placeholders, table QR, visit balance/cart, order confirmation, member login, Wi-Fi welcome and Wi-Fi internet sheet at 320, 360, 390, 430, 768, 1280 and 1440 px.
4. On site, use an unauthenticated or test guest account: explore menu, options, cart totals, errors and add-to-visit. Test actual order submissions only under authorized test conditions.
5. Validate the captive portal on a device **without Internet connectivity**; verify menu and Wi-Fi choices, captive authentication forms, dismiss buttons, daily access code, network status and Retry. Do not replace the HotSpot auto-submit with an app-page-only solution.
6. Confirm the unchanged staff and provider layouts, password/OTP/CSRF behavior and publication privacy.
7. Check accessibility and sufficient text contrast, including invalid forms and error states.
8. Only then merge / release via the tested GHCR digest deployment workflow. Production deployment and acceptance remain separate.

**Operational note:** previous source-code merges alone did not restyle the homepage; styling must first pass these checks and then be deployed.  
