# Hub Suite UI 3.0 — mobile-first visual pilot

Status: **draft visual pilot, not deployed or approved for production**.

## Scope

- Public menu `/menu/` and table-QR menu `/menu/table/<qr_token>/`: stronger type and turquoise hero, two compact featured category cards (the first two **real** menu sections), optional decorative real product media, compact mobile product tiles, clear search and category rail.
- Staff `/staff/` Operations workspace: neutral surfaces, smaller semantic accents, readable account and order cards, compact controls and horizontal mobile KPI rail.
- Opt-in `hub-ui-v3` body class. No other staff pages or provider pages receive the pilot styling.
- Keep existing logo URLs, all Django views, forms, order / payment / Internet behavior, QR links, staff permission checks, and contextual drawers.

## Design tokens and typography

The new `static/css/hub_ui_v3_pilot.css` stylesheet is loaded **after** existing customer/staff v2 layers. It defines `--hub-v3-*` tokens, then maps the pilot's semantic variables only within `.hub-ui-v3`.

| Meaning | Token | Hex |
| --- | --- | --- |
| Brand accent | `--hub-v3-turquoise` | `#1ABC9C` |
| Accessible action color | `--hub-v3-primary` | `#137F70` |
| Food accent | `--hub-v3-food` | `#E67E22` |
| Events accent | `--hub-v3-events` | `#9B59B6` |
| Kids accent | `--hub-v3-kids` | `#F1C40F` |
| Text | `--hub-v3-ink` | `#34495E` |
| Background | `--hub-v3-canvas` | `#F7F8F8` |
| Dividers | `--hub-v3-border` | `#DCE4E7` |

Typography: IBM Plex Sans Arabic 300/400/500/600/700, with Noto Sans Arabic / Tahoma fallbacks. The font CSS is requested **asynchronously** from Google Fonts for pilot pages only. If it cannot be reached, the system remains readable with its local fallback. Before production approval, evaluate self-hosting a properly licensed webfont to remove the external dependency and support weak connections.

Existing logo/brand icon files are **not modified**. Where media exists, feature cards reuse already-associated product media; otherwise they render as plain colored blocks. They do not assume that section 1 is drinks or section 2 is food; color is a visual treatment, not a category mapping.

## QA gates

1. Run `python manage.py check` and `python manage.py test core.tests.test_hub_ui_v3_pilot core.tests.test_staff_design_system core.tests.test_customer_experience_pass core.tests.test_public_menu_mobile_contract`.
2. Render menu with 0, 1 and 2+ active sections, with images allowed/disabled, plus standard and table QR routes.
3. Manually test keyboard focus, screen-reader label for feature navigation, cart quantity, modifiers, order submit, receipt confirmation and back navigation.
4. Test phone widths 360 / 390 / 430px, tablet ~800px, and desktop ~1280px; both long Arabic product titles and no-media products.
5. Verify staff roles: only authorized buttons/actions remain available, account drawer opens, payments and internet panels still work.
6. Check low-bandwidth/captive connections: optional font and lazy-loaded feature media cannot block the form, cart or navigation.
7. Confirm print and staff/mobile menu selectors do not regress. Assess contrast in the real browser for text over live menu images.

## Rollout

Branch → draft PR → CI/static review → staging deploy → actual device/RTL check → manual approval → merge/deploy. Do not change MikroTik/RouterOS, migrations, production settings or production resources as part of this visual pilot.

No production deployment is authorized by this PR.
