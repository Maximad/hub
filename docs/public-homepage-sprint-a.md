# Public homepage — Sprint A implementation

Implements Sprint A from the [specification branch](https://github.com/Maximad/hub/blob/docs/public-homepage-v1-spec-20261031/docs/public-homepage-v1-brief-2026-10-31.md). This branch is separate from the design brief. No migration, content seeding, booking, payment, router, or deployment change is included.

## Release gate and routes

`PUBLIC_HOMEPAGE_ENABLED` defaults to `false`. With it disabled, `/` retains the legacy dashboard and the new event routes return 404. In local development, set it to `true` and restart the Django process to review:

- `/`: Arabic RTL venue introduction and up to three current published events.
- `/events/`: upcoming and ongoing published events, 12 per page.
- `/events/<uuid>/`: published public event details. Past published records remain readable with a past-date notice.

`dashboard` still reverses to `/`. The new public shell links its brand to `/`, keeps `/menu/` and `/me/` immediately reachable, and extends the existing template's single main landmark. Existing menu/account/QR headers and transactional routes remain unchanged. New base-template blocks preserve their existing default contents.

## Publication and privacy

The shared read-only service filters `status=published` and `is_public=True`. Current events are future starts, or started records with an end strictly after now; no-end records disappear after their start. Equal start times sort by PK. Dates display in Asia/Damascus regardless of the active visitor timezone.

Cancelled, completed, draft, deleted and private events return 404. The existing schema has no durable evidence of prior publication, so cancelled records are not publicly exposed. Every request rechecks eligibility; public responses use `never_cache` to avoid stale promotion after withdrawal. Previously downloaded pages cannot be revoked.

The presentation dataclass exposes only title, description, public UUID-based URL, dates, safe image URL/alt, and temporal state. No rooms (no room publication flag exists), capacity, ticket prices, staff notes, vendor contacts, membership metadata, or model instances are passed by the public service. Only active cover/poster relations on active image/GIF assets are eligible. No gallery-only images, thumbnail writes, or remote fetches occur on public reads. Missing files/failed image requests use the text design fallback.

The static hospitality/program introduction makes no opening-hours, current-program, booking or price promises. No unverified contact details or map links are invented. Programs/pop-ups/memberships discovery and editorial scheduling remain out of scope.

## Validation

Use the repository's Python 3.12/PostgreSQL setup and environment variables in `docs/testing.md`:

```bash
PUBLIC_HOMEPAGE_ENABLED=true scripts/test-full.sh --noinput
scripts/test-full.sh core.tests.test_public_homepage --noinput
python manage.py collectstatic --noinput
```

The focused suite covers public route access, safe methods, boundaries, chronological/stable ordering, eligibility revalidation, empty and missing-image states, escaped content, bounded query count, pagination, Damascus time, release rollback, and existing menu/QR/account/staff/provider entry points. The complete suite exercises the unchanged customer, finance/payment and internet workflows.

For offline responsive checks, install Playwright in the development virtual environment and provide a system Chromium (or set `CHROMIUM_EXECUTABLE`):

```bash
PYTHONPATH=. python scripts/check-public-homepage.py
```

Checks 320/360/390/430/768/1280px, one H1/main, RTL, long unbroken titles, mobile navigation by keyboard, and broken-image fallback. Screenshot output defaults to `/tmp/hub-public-homepage-preview` and can be selected with `PUBLIC_HOMEPAGE_PREVIEW_DIR`.

## Screenshots for draft review

The committed desktop/mobile captures use **synthetic layout-only event copy**, no database changes and no invented poster photography. They are design previews, not event announcements. Actual approved venue/event imagery is not available in this checkout. Existing approved active event artwork will render from the source records when configured.

| Page | Mobile | Desktop |
| --- | --- | --- |
| Home | [390px](screenshots/public-homepage-sprint-a/home-390.png) | [1280px](screenshots/public-homepage-sprint-a/home-1280.png) |
| Events | [390px](screenshots/public-homepage-sprint-a/events-390.png) | [1280px](screenshots/public-homepage-sprint-a/events-1280.png) |
| Detail | [390px](screenshots/public-homepage-sprint-a/detail-390.png) | [1280px](screenshots/public-homepage-sprint-a/detail-1280.png) |
| Empty home | [390px](screenshots/public-homepage-sprint-a/empty-390.png) | [1280px](screenshots/public-homepage-sprint-a/empty-1280.png) |

Before a separate release decision: approve Arabic copy and branding, inspect staging with actual approved images, verify the deployed revision and operational links, then explicitly enable the flag. This implementation does not authorize merging or deployment.
