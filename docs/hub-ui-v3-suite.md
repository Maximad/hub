# Hub Suite 3.0 — suite-wide UI release

Status: implementation branch, **not a production deployment**.

## Coverage

All Django pages extending `templates/base.html` automatically receive `hub-ui-v3` and `static/css/hub_ui_v3_suite.css`, loaded after the legacy styles. The existing scoped menu + Operations pilot remains; the new layer extends typography, semantic colors and shared components to the rest of the system.

| Surface | Scope | Character |
| --- | --- | --- |
| Public menu, QR ordering and product cards | Existing v3 pilot + suite layer | Expressive turquoise, colored real categories, compact transactions |
| Visit/session, order confirmation, table entry | Base + suite layer | Friendly, light, clear status and service actions |
| Member login/account | Suite layer + semantic markup polish | Readable mobile cards, QR and tables |
| Staff Operations, POS, kitchen/preparation | Base + suite layer + existing v2 layouts | Dense operational design, restrained accents |
| Staff finances, reports, inventory, members, events, reservations, imports, settings and related forms | Base + suite layer + existing staff foundation | Shared font, colors, controls, tables, navigation and responsive patterns |
| Internet/provider portal including login | Base + suite layer + provider CSS | Blue accent for network work, clear status colors |
| Django admin | Dedicated `admin/css/hub_admin_v3.css`, scoped class | Preserves native admin structure/actions |
| Standalone Wi-Fi entry/staff login | Local v3 CSS added | No external font/network dependency |
| Print, tickets and receipts | Existing print styles preserved | Neutral/legible output |

This is a **suite-wide component/theme migration**. It is not a claim that each form has been individually restructured. Any remaining page-specific UX issues should be handled as separate functional improvements, not ignored or papered over with global `display` overrides.

## Style contract

- Original logo, existing image URLs and branding text **unchanged**.
- IBM Plex Sans Arabic in Thin-adjacent Light 300, Regular 400, Medium 500, SemiBold 600, Bold 700. Decorative light weights only for large text, not operational instructions.
- Turquoise `#1ABC9C` for editorial/brand areas; accessible action teal `#137F70`, orange `#E67E22` food, purple `#9B59B6` events, yellow `#F1C40F` kids, blue `#2980B9` internet.
- Staff defaults to `#F7F8F8` background, white cards, ink `#34495E`, borders `#DCE4E7`.
- Semantic statuses retain their operational meanings; no relying on color alone.
- No new dependencies or database migrations. Existing JS and business logic unmodified.
- Public and staff IBM font request is optional/asynchronous; the standalone captive portal uses no external font request.
- Preserves native form actions, names, CSRF protection, URL routes and role gates.

## One-checkpoint workflow (credit-conscious)

1. Make all desired visual changes on **one branch** without opening a PR. The repository CI runs for PRs and main pushes, not every ordinary feature-branch commit.
2. Run cheap contract/static checks during editing and **one full PostgreSQL Django CI** when the branch is ready via a PR.
3. Fix only concrete CI failures; avoid repeated full test runs for cosmetic refinements.
4. Approve merge once CI passes. This is a shared-style change across many pages, so a **small manual spot check of one customer mobile page, one staff phone/desktop page, provider, and admin** remains advisable even if staging is skipped.
5. Deploy via the existing `scripts/deploy-production.sh` (backup and restore verification included) from the `deploy` account, retaining real operational records when appropriate with `ALLOW_PRELAUNCH_OPERATIONAL_DATA=true`.
6. Confirm routes and static assets, and compare `backups/last_deployed_revision.txt` with merged HEAD. Use documented rollback procedure if a functional regression appears.

## Minimal post-deploy smoke

- `/menu/`: search, category anchors, product details, cart, checkout, short mobile viewport and images.
- `/staff/`, POS + kitchen: authorized account actions, navigation, drawer, preparation status.
- `/provider/`: login, navigation and information on subscription/network pages; **no MikroTik edits**.
- `/admin/login/`: accessible login/form controls and dashboard.
- Wi-Fi entry: verify only if physically on the captive network, avoid remote RouterOS experiments.
- Verify print/export stays black-on-white and does not lose content.

## Boundaries

No schema, prices, payments, refund rules, POS behaviors, network/router configurations, access control, data/imports or operating settings changed. Visual alignment and accessibility still need device-level verification. Automated tests do not certify pixel-perfect rendering of every page.
