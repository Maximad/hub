# Public site readiness report

Run this read-only command with the settings and database for the environment being reviewed:

```bash
python manage.py public_site_readiness
python manage.py public_site_readiness --json
python manage.py public_site_readiness --json --strict --limit 500
```

The report reuses the public discovery gates, homepage feature resolver, commercial membership preflight, attendance intake gate and effective staff capability checks. It reports aggregate counts only: no contact details, owner/IP digests, request locators, editorial copy, plan metadata, internal commercial findings or image URLs.

It checks:

- The public homepage flag in the command process. A disabled flag produces a warning and remains disabled.
- Current public event, program and pop-up counts. Empty optional sections are supported.
- Actual editorial resolution and fallback. An ineligible selected source produces a warning; an unconfigured, disabled or out-of-window slot uses normal fallback.
- Configured, eligible public event and pop-up artwork that has no usable safe URL, and unusable image overrides on a selected homepage feature. Records with no configured public image intentionally use text fallback. External URL availability and visual quality are not checked.
- Active customer-visible membership candidates using the same commercial preflight that hides unsellable plans. Only blocked/advisory totals appear; a plan can have both findings.
- Future per-event attendance opt-ins versus eligible intake events. Private/unpublished events remain closed. Historical opt-ins do not create permanent warnings, and zero opt-ins is supported.
- Pending requests older than 24 hours, pending requests whose event no longer accepts requests (including deleted events), and approved requests missing a linked reservation. The 24-hour threshold is an operational review hint, not an expiry or rejection policy. Counts can overlap.
- Active users with effective `reservations` capability when intake or outstanding work needs staff. Access profiles, per-user overrides and the superuser emergency path use the existing permission helper. This does not establish staff availability or shift coverage.

Event and pop-up media inspection, membership preflight and reviewer inspection each default to the first 100 records in stable order. `--limit` accepts 1–500 per sampled section. Each section reports its total, inspected count and truncation; incomplete coverage always warns. Program counts and attendance queue counts use aggregate queries. Large installations must separately review records beyond the maximum sample. Counts and rows can change while the command runs; the report does not lock operational records or guarantee capacity.

Normal mode exits successfully with warnings; `--strict` exits nonzero after writing the complete report when any warning exists. This supports machine-readable review without changing publication flags to obtain a clean result. Empty configured content with the release flag enabled can pass; that does not mean a release is approved.

The command reads the connected database and configured media storage, including the existing storage existence checks. It does not save records, generate thumbnails, fetch external image URLs, submit requests, reserve seats, send notifications, sell memberships or provision internet access. It does not verify a deployed revision, the settings of a running web process, public HTTP routes, approved copy/artwork or a staging browser review. Follow the existing [public homepage brief](public-homepage-v1-brief-2026-10-31.md) and [attendance notes](public-event-requests-sprint-d.md) for those checks. Running the command enables nothing and deploys nothing.

Validation:

```bash
python manage.py test core.tests.test_public_site_readiness --noinput
PUBLIC_HOMEPAGE_ENABLED=true scripts/test-full.sh --noinput
```

Use the full suite's fresh database rather than `--keepdb`; transaction tests can flush migration-seeded configuration.
