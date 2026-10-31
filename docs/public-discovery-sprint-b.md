# Public discovery — Sprint B

Extends Sprint A with Arabic RTL public program, pop-up, and membership discovery. No registration, booking, checkout, pricing policy, membership activation, router, or deployment workflow is added.

## Routes and release

All six new routes share `PUBLIC_HOMEPAGE_ENABLED`, which still defaults to false:

| Index | Detail |
| --- | --- |
| `/programs/` | `/programs/<code>/` |
| `/popups/` | `/popups/<uuid>/` |
| `/memberships/` | `/memberships/<code>/` |

The homepage adds up to three cards per section and the public navigation links to each index. Index pages examine at most twelve candidates per page. Details and lists recheck publication/readiness on every request with no-store responses. Only GET/HEAD are accepted. Existing menu, QR, staff, account, payments, membership sales and internet endpoints are unchanged.

## Publication gates

Two additive migrations introduce four boolean `is_public` fields, all defaulting to **false**, including for existing rows:

- `Program`: additionally requires `is_active=True`, `status=open`, and no end or an end strictly after now. Null start dates are allowed and shown honestly as not yet announced; dated programs sort chronologically with PK tie-break, undated last.
- `Vendor` and `VendorParticipation`: both must be explicitly public, vendor active, participation status **active**. Future starts and ongoing records with a future end qualify. A no-end record stops being current after its start. Planned, completed and cancelled participations are never promoted. Sprint B does not add advance-publication approval for planned records.
- `VendorMedia`: explicit independent public approval is required, as well as an active relation, active image/GIF asset, and cover/logo role. Gallery-only, inactive, private, missing or unsafe media does not render. No derivatives or other storage writes occur on public GETs.

Publication is edited through existing superuser-only Django admin. Default Django `LogEntry` auditing records edits; no publication action or automatic bulk approval is introduced. Existing staff creation/import flows retain the false defaults. Review titles/descriptions and artwork before selecting these flags.

Memberships reuse `is_active` + `visible_to_customer`. The existing read-only commercial readiness assessor must return `can_sell=True`; known broken internet allocations/configuration hide the plan on home, list and detail. Internal readiness errors/warnings are never sent to the public template. Readiness checks run only for a bounded candidate page (12, or 3 on home), so a page can contain fewer cards, including none, when candidates fail readiness. Pagination still reaches later candidates. Readiness is a configuration assessment, not a promise of network availability or public checkout.

## Safe public fields

The service returns immutable, explicitly allowlisted presentation records rather than ORM objects. Programs expose approved Arabic name/description and dates. Pop-ups expose title, vendor Arabic public name, dates, and approved media. It never calls the vendor string fallback, which can contain a phone number. Linked internal events and locations are not exposed.

Memberships expose approved name/description, actual `price_syp` (including zero), and the existing authoritative term summary. Staff benefit labels and configured-only perks are deliberately omitted; publication does not promise automated discounts, reservation priority, internet packages, or other unverified redemption behavior. There is no purchase CTA; existing account/menu links remain available.

Vendor contacts, phone numbers, commissions, settlement/participation notes; program metadata/capacity; plan notes, benefit metadata, subscription/member details, partner allocations and other internal findings never enter the public presentation record. All text is autoescaped. Times display in Asia/Damascus. Withdrawal, cancellation, expiry or readiness failure returns 404 for canonical detail URLs rather than exposing prior private content.

## Validation and screenshots

```bash
# Use the existing Python 3.12 + PostgreSQL development environment.
python manage.py migrate --noinput
PUBLIC_HOMEPAGE_ENABLED=true scripts/test-full.sh --noinput
scripts/test-full.sh core.tests.test_public_discovery core.tests.test_public_homepage --noinput
python manage.py collectstatic --noinput
PYTHONPATH=. PUBLIC_HOMEPAGE_PREVIEW_DIR=/tmp/hub-sprint-b-preview python scripts/check-public-homepage.py
```

Focused coverage includes real migration of legacy rows with private defaults, publication/status/date gates, withdrawal, missing/unsafe/private media, private-field exclusion, existing membership readiness, exact price and term, no database writes on public GET, rejection of mutations, pagination, bounded candidate work/query counts, superuser-only admin publication and admin audit.

Browser checks cover all six viewport widths (320/360/390/430/768/1280), long Arabic text, one main/H1, keyboard navigation/focus, primary-action contrast and broken-image fallback. These committed captures contain **synthetic layout-only data**, not public commitments or approved imagery:

- Homepage: [desktop](screenshots/public-discovery-sprint-b/home-1280.png), [mobile](screenshots/public-discovery-sprint-b/home-390.png).
- Programs: [list](screenshots/public-discovery-sprint-b/programs-390.png), [detail](screenshots/public-discovery-sprint-b/programs-detail-390.png).
- Pop-ups: [list](screenshots/public-discovery-sprint-b/popups-390.png), [detail](screenshots/public-discovery-sprint-b/popups-detail-390.png).
- Memberships: [list](screenshots/public-discovery-sprint-b/memberships-390.png), [detail](screenshots/public-discovery-sprint-b/memberships-detail-390.png).

Migrations here are validated only on the isolated development PostgreSQL database. Production migrations, approval of actual public records/images/copy, staging review and release enablement remain separate work. Nothing in Sprint B publishes existing programs, vendors or vendor images automatically.
