# Public editorial promotion — Sprint C

One optional homepage editorial slot extends Sprint A/B. No CMS, booking, registration, payment, finance, router, or membership policy change is included. `PUBLIC_HOMEPAGE_ENABLED` still defaults to false. No actual feature/content is seeded or published by the migration.

## Editing and publication

Superusers use **Core → المحتوى البارز للرئيسية** in the existing Django admin. There can be at most one `HomepageFeature`, enforced by its fixed primary key plus a database check. New records are disabled and unpublished. The editor stores:

- Source kind: event, program, vendor participation, or evergreen venue introduction.
- Exactly the matching source reference (none for evergreen).
- Independent enabled and published toggles.
- Optional start and end times: start inclusive, end exclusive; blank bounds are open-ended. End must be after start when both exist.
- An optional approved Arabic summary, up to 300 characters, and approved image asset.

The canonical source title and destination are derived from authoritative records. Editors cannot enter arbitrary external CTA URLs, prices, capacity, or unverified booking links. Publishing an image override explicitly approves that image's public use; it must still be an active image/GIF with a safe, available URL. Otherwise the source's own eligible image/text design is used. Preview and public reads never create thumbnails.

The admin uses its existing superuser-only access policy and normal Django `LogEntry` audit. Saves additionally record `homepage.feature.updated` in `ActivityLog`, including actor, current source/image reference IDs, publication toggles, schedule and changed field names. Summary text and private source data are not duplicated into this activity payload. Deletion uses the normal Django admin deletion audit.

## Resolution and fallback

Every homepage request resolves fresh data, in this order:

1. The single enabled + published feature inside its schedule, **only if its referenced source is still publicly eligible**.
2. The earliest eligible public published current event, ordered by start time then PK as in Sprint A.
3. The existing evergreen venue introduction when there is no eligible event.

An explicit eligible evergreen editorial selection wins over events and can carry the approved summary/image. An invalid/deleted event/program/participation reference never turns into an evergreen selection carrying stale promotional copy. Its summary and override image are discarded before fallback.

Event sources reuse the published + public, upcoming/ongoing filters. Program sources reuse explicit public + active + open + not-ended gates. Participation sources reuse both publication flags, active vendor and participation, and current/upcoming dates; planned participations stay private. Membership promotion remains outside this slot; existing membership discovery is unchanged.

References use `SET_NULL` on deletion. This keeps the editorial record for review, while the next public request selects a safe fallback. Editing an orphaned record requires choosing a valid replacement/reference (or clearing references and selecting evergreen); disabling/deleting it is not a publication operation. Database checks reject incompatible reference kinds and invalid windows. Ordinary model `save()` does not replace form/full validation; service-level eligibility remains mandatory on reads.

## Customer-layout preview

The changelist and saved-object edit page link to the **saved** feature preview at `/admin/core/homepagefeature/preview/`. Unsaved form edits are not previewed. The endpoint is private, read-only GET/HEAD, and requires an active staff superuser under the existing admin policy. It works before the global public release flag is enabled.

- **Draft mode** ignores the feature's enabled/published toggles and schedule. It still enforces all public source gates, so even an editor preview never reveals a draft/private event, unpublished program/vendor, or the editorial image/summary attached to an ineligible source.
- **Schedule simulation** uses the actual feature toggles/window and the chosen time. Naive datetime inputs are interpreted as Asia/Damascus. The banner reports selection or fallback reason.

The preview does not save anything or activate the release flag. Dates are simulated against **current** publication/status records; it is not a historical publication audit or a forecast of future publication changes. Preview links use the real public routes, which remain gated until the global release flag is enabled. No signed public draft-sharing URL is introduced.

## Cache behavior

There is no feature/result cache. Public responses retain `Cache-Control: no-store` through `never_cache`; edits, source withdrawal, deletion and schedule expiry are observed on the next request without manual eviction, worker jobs, or a TTL. The preview is also no-store, varies by Cookie, and sets `X-Robots-Tag: noindex, nofollow`. This does not revoke pages already downloaded by a visitor.

## Validation

```bash
python manage.py migrate --noinput
scripts/test-full.sh core.tests.test_homepage_feature core.tests.test_public_homepage core.tests.test_public_discovery --noinput
PUBLIC_HOMEPAGE_ENABLED=true scripts/test-full.sh --noinput
python manage.py collectstatic --noinput
PYTHONPATH=. PUBLIC_HOMEPAGE_PREVIEW_DIR=/tmp/hub-sprint-c-preview python scripts/check-public-homepage.py
```

Focused tests cover all source types, source withdrawal/cancellation/expiry/deletion, explicit evergreen selection, schedule boundaries, private summary/image exclusion, missing/unsafe images, source-reference validation, singleton/database constraints, no public/preview writes, immediate edit visibility, preview time parsing, superuser-only access, existing public release behavior, admin auditing and prior public discovery behavior.

Browser checks exercise event/program/participation/evergreen hero states and editor preview at 320/360/390/430/768/1280px, RTL, one H1/main, no overflow, long Arabic copy, keyboard navigation/focus, action contrast and missing-image fallback. These review images use **synthetic layout-only records and copy**, not approved real events or photography:

- Private preview: [desktop](screenshots/public-editorial-sprint-c/editor-preview-1280.png), [mobile](screenshots/public-editorial-sprint-c/editor-preview-390.png).
- Featured program: [desktop](screenshots/public-editorial-sprint-c/featured-program-1280.png), [mobile](screenshots/public-editorial-sprint-c/featured-program-390.png).
- Featured participation: [mobile](screenshots/public-editorial-sprint-c/featured-popup-390.png).
- Evergreen editorial slot: [mobile](screenshots/public-editorial-sprint-c/featured-evergreen-390.png).

Production migration, actual content/image/copy approval, staging inspection and release enablement remain separate. No production deployment or content publication is performed by this work.
