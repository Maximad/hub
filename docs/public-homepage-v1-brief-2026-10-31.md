# Hub Sweida — Public Homepage v1 | Codex Implementation Brief
**Date:** 2026-10-31  
**Status:** Design/development specification for review; **NOT an approval to deploy**.  
**Owner:** Hub Sweida; Hub Suite repository `Maximad/hub`.  
**Approved positioning:** Public website for a **cultural venue with services and events**, not an ecosystem directory or administrative dashboard. See the [approved positioning decision](https://app.notion.com/p/40ab3fed68f881ff9bfaec1d9428c80a).

## 1. Purpose and success definition
A visitor arriving at `/` should, without logging in, be able to:
1. Recognize Hub Sweida and see why to visit.
2. Discover **published, current** events and open experiences; view details.
3. Reach the existing public menu.
4. Understand rooms/hospitality, public programs, pop-ups and memberships.
5. Find verified visiting information and the existing member account.

The homepage is **not** a dashboard, a new e-commerce backend, or a Habaq project directory. Event / food / memberships must point to authoritative Hub Suite records. No synthetic schedules, estimated capacities, unsupported discount promises, or unverified booking CTAs.

## 2. Source code audit — observed on default branch 2026-10-31
- `config/urls.py` maps `path('', menu.dashboard, name='dashboard')`. `templates/core/dashboard.html` is a legacy `لوحة مشاريب` placeholder linking to staff routes. Change root to a public view while retaining its named URL where compatibility requires. Audit reverse() consumers before renaming.
- `/menu/` and table QR flow are already live routes. Do **not** alter ordering, checkout, QR, POS or user visits.
- Member experience: `member_accounts/urls.py` provides `/me/`, `/me/login/`, invitation flow; customer header in `templates/base.html` currently links its brand to `/menu/`. Homepage public shell should link brand to `/` and give prominent dedicated menu link; guard any public menu/cart/QR assumptions.
- `events/models.py:Event` has `status` (draft, published, cancelled, completed), `is_public`, `starts_at`, `ends_at`, `title_ar`, `description_ar`, `uuid`, optional `room`, `capacity`. `EventMedia` supports poster/cover/gallery and is_active; `EventTicketType` supports ticket types, price and capacity, but a functioning anonymous ticket purchase is **not** established by this audit.
- `members/models.py:MembershipPlan` has `is_active`, `visible_to_customer`, descriptions, periods, price and benefits; some benefit rules may be configured but not commercially ready. Do not promise unverified perks or implement a new subscription sale flow.
- `members/models.py:Program` has `status`, `is_active`, name, description, dates, capacity, `code`, but **no public-visibility flag** in inspected model. ProgramEnrollment exists, but there is no verified anonymous enrollment route.
- `vendors/models.py:VendorParticipation` has `status` (planned/active/completed/cancelled), dates, vendor, event and location. `VendorMedia` supports public-facing visuals but the vendor includes private contacts, commissions, settlement notes. **Neither Vendor nor Participation has a reviewed explicit public-visibility flag**. Never serialize private fields to visitors.
- Only staff event and reservation routes were located in `config/urls.py`; do not link public cards to `/staff/events/` or `/staff/reservations/`.
- `templates/base.html` has one main element, customer header and route-specific CSS. Reuse existing Django server-rendered templates/HTMX and CSS infrastructure; avoid a separate React app, generic CMS or new DB.
- Actual deployed production revision and the live public page were **not** validated as part of this brief. Check staging and production revision before release.

## 3. Approved intent versus proposed implementation
**Approved:** cultural-venue-with-services-and-events positioning.  
**Proposed (must be reviewed in this PR):** layout and copy, color palette, public pages, publication flags, homepage editorial control, SEO strategy, data filters and phased rollout. No event, venue opening time, booking method, price, Habaq partner or financial arrangement is approved by this document.

## 4. Information architecture / proposed URL scheme
| Public route | Purpose | Source |
| --- | --- | --- |
| `/` | Curated venue homepage | public context service + small homepage settings |
| `/events/` | Published upcoming/ongoing events | Event |
| `/events/<uuid:uuid>/` | Public detail, including cancellation status when previously public | Event + active EventMedia |
| `/programs/` | Publicly approved open programs | Program |
| `/programs/<slug:code>/` | Public program detail | Program |
| `/popups/` | Publicly approved projects/guest pop-ups | VendorParticipation |
| `/popups/<uuid:uuid>/` | Public pop-up detail with approved vendor media | VendorParticipation + limited Vendor |
| `/memberships/` | Public comparison of ready/visible membership plans | MembershipPlan |
| `/menu/` | Existing ordering/menu; **unchanged** | Current routes |
| `/me/`, `/me/login/` | Existing member account; **unchanged** | Current routes |

Phase 1 need not ship every detail route simultaneously, but **every displayed CTA must have a functioning public target**. Use clearly labelled info/visit/contact action rather than claiming `احجز الآن` if checkout or booking is not ready.

## 5. Page hierarchy and reusable components
**Header**: Hub mark, `الفعاليات`, `المكان`, `الأكل والشرب`, `البرامج`, `العضويات`; `المنيو` and `حسابي` immediately reachable. Responsive menu: simple accessible drawer, no app-only navigation requirement. Brand logo returns to `/`.

**Primary hero (first viewport):**
- Editorial venue photo / published featured event; Arabic heading (proposed: `مكان بيجمع أكتر من فكرة`); single primary CTA `شوف شو في بالهَبّ` / event detail and secondary `المنيو`.
- Optional compact upcoming-activity teaser, provided it is a real public published record.
- If featured record is unpublished, cancelled, expired, or deleted, use next eligible public event; otherwise evergreen Hub intro. Never let a stale promotional image imply an event is happening.
- Original Hub venue photographs preferred. No generic AI stock photos in production unless explicitly approved.

**Upcoming events**: maximum 3 next eligible records (date, time Asia/Damascus, short title, room when public, primary event photo). `كل الفعاليات` public page. When empty, show a concise invitation/venue message, not fabricated cards.

**Experience / hospitality**: fixed editorial tiles for `مشاريب`, `سُفرة`, `أوضة`, `الجنينة`; only mark an area as open/current if verified, otherwise omit specific operating claims. `المنيو` links existing `/menu/`.

**Programs**: maximum 2–3 approved public programs. No public cards solely due to `status=open` without an additional explicit publication gate.

**Pop-ups and collaborations**: maximum 2–3 explicitly public current/upcoming participations with dates, approved media and **public name only**. Never display internal vendor phone, commercial settlement, commissions, contact person, private notes.

**Membership and services**: show only `MembershipPlan.is_active=True` and `visible_to_customer=True`, with safely supported benefits and current price. Avoid `اشترك الآن` unless a verified public purchase path exists. Internet page/packaging can be a descriptive link only if available; no MikroTik changes.

**Visit / footer**: source-verified address/map link, hours (if configured and verified), phone/contact (only from approved public venue settings), accessible social links, direction to venue.

## 6. Public data contracts & safety invariants
Use one read-only `public_homepage_context(...)` service (or equivalent) that returns a normalized view model for the template, not models with unrestricted fields. Avoid duplicate business truth.

**Event eligibility**:
- `status='published' AND is_public=True`.
- Upcoming: `starts_at >= now`. Ongoing: `starts_at < now` and `ends_at IS NOT NULL AND ends_at > now`. Treat single-start/no-end events as upcoming until start, then remove from upcoming (don't assume ongoing indefinitely).
- Sort by starts_at, stable tie-break by PK. Use timezone-aware `timezone.now()` and display in `Asia/Damascus`.
- Detail routes for canceled *previously published* events need an explicit approach: a canceled record may remain accessible via an existing canonical link to display cancellation, but should never appear as promoted/current. Do not infer previous publication history from `status` alone; if no durable publication evidence, choose public 404 rather than leak a draft or private event.
- Missing images: styled text fallback; no broken media or placeholder pretend posters.

**Program eligibility**: `is_active=True`, `status='open'`, **new explicit public flag default False**, and program not already ended. If publishing flag absent in current code, implement gated public access rather than exposing every open internal program.

**Vendor/pop-up eligibility**: active public vendor + participation with **new explicit public flag(s) default False**; relevant planned/active dates; never expose supplier/private operational records. A `planned` record is not approval to advertise. For v1 allow a confirmed `active` record for display; add a separate publication approval mechanism for advance advertising. Date-eligible content alone is not proof of public publication.

**Membership eligibility**: active + visible_to_customer; only public-friendly descriptions and actual prices, no staff notes/metadata, no unsupported-benefit claims. No login required to browse.

**Editorial override**: at most one minimal HomepageFeature configuration with source reference (event/program/participation/evergreen), scheduled start/end, enabled/published, and image/summary. Validate referenced public eligibility on every public read (including cache revalidation). If no eligible override, choose nearest upcoming published event then evergreen intro. No generic drag-and-drop CMS.

**Input and output security**: public read-only routes; safe fields only; no internal IDs/secret tokens shown to people; Django autoescaping for text, safe URLs, optimized lazy media, no contact leakage. Staff editing uses existing capability checks and audits. Query counts bounded/preselect_related, production data remains untouched during tests.

## 7. Visual design system (proposed, homepage-local)
- Character: intimate cultural venue / warm concrete and wood / contemporary editorial collage, understated antique detail, real event artwork.
- Public palette proposal: background `#F4F0E8`, ink `#202722`, deep-green `#223A34`, warm accent `#C98D5B`, paper card `#FFFCF6`. Check WCAG contrast on real photos; use dark overlays only as needed.
- Arabic-first RTL. Reuse existing IBM Plex Sans Arabic + local fallback from base shell; do not introduce a blocking external font dependency.
- Type: strong editorial hero heading; compact practical card captions; date/time visually distinct; no excessive badge clutter.
- Desktop: hero 2:1 main feature/next-up rail; 3-column event cards; hospitality 2-column; programs/pop-ups horizontal editorial strips; footer.
- Mobile 360/390/430: one-column hero, immediate event/menu actions, 1-column event list, tappable cards, no horizontal overflow; only add a fixed bottom nav after checking interference with menu/cart/QR.
- Semantic landmarks, a single H1, links/buttons with accessible names, focus states, reduced-motion support, alt text, lazy-load below fold; don't place critical copy only inside images.

## 8. Scope boundaries / phasing
**Sprint A — read-only public foundation (first Codex implementation task):**
1. New public root view + template, public CSS namespace, responsive header/footer, safe homepage context service.
2. Dynamic **event list and event detail** based on existing public eligibility; real media/fallbacks. Existing menu/account links.
3. Static venue/food/participation intro tiles, but **no false active/open status** or dead CTA.
4. Tests for route, anonymous access, publication, ordering, missing image, zero events, correct time formatting, RTL, unchanged staff/menu/QR.
5. A feature flag / safe release mechanism if consistent with existing settings; keep deployment separate.

**Sprint B — expanded discovery (proposed):**
- Public programs, pop-ups, memberships indexes and detail pages; add minimal explicit visibility flags default False and migrations. Safe approved profile fields, readiness filtering, no private contacts.

**Sprint C — editorial promotion (proposed):**
- Minimal featured-content scheduling/admin controls and audit, fallback logic, customer preview, cache invalidation. No whole CMS.

**Sprint D — conversion workflows (separate decision):**
- Public booking/registration/payment only after current transactional APIs, capacity limits, finances, cancellation and approval responsibilities are reviewed. No new billing logic authorized by this brief.

No router/MikroTik work, POS/cash changes, membership commercial policy, Habaq editorial or financial coupling, or production deployment as part of the spec.

## 9. Required tests / acceptance
- Anonymous GET `/` = 200 and renders Hub identity, venue services, and menu/member navigation; no staff links or private values.
- Publish an event, confirm it appears chronologically and its public detail works; draft/private/cancelled/completed event never appears in upcoming list.
- Ongoing and no-end event boundary tested with timezone-aware frozen times; no completed event advertised.
- Removed image gracefully falls back. No event case shows honest evergreen messaging.
- User with no login can browse, but cannot mutate events, programs, vendors, payments, settings or membership. No public POST endpoints introduced by Sprint A.
- Header brand link changes must not break existing ordering / table QR / customer-space navigation.
- Program / vendor publication flags in Sprint B default to False on migration; existing records do **not** become public as a side effect.
- Membership public display never includes internal benefit metadata, staff notes, vendor/partner settlement, member details.
- Responsive viewport checks 320/360/390/430/768/1280; long Arabic words, no overflow, keyboard/focus, accessible contrast.
- Run full existing Django/PostgreSQL CI, `python manage.py check`, route and migration checks, targeted regression coverage, static asset collection on staging. Verify no accidental second `<main>` landmark.
- Staging visual inspection with **real approved Hub images**, user approval of copy/date/links; separate merge and production release authorization.

## 10. Codex handoff (copy as implementation instruction)
> Implement **Sprint A only** from `docs/public-homepage-v1-brief-2026-10-31.md` on a feature branch of `Maximad/hub`. Inspect current main before editing. Use existing Django template/HTMX patterns, keep existing services intact, and build a beautiful accessible Arabic RTL public homepage at `/` with data-backed published upcoming events and a safe event detail route. Preserve public menu, member account, QR ordering, staff, provider, Internet and finance flows. Do not introduce public booking, new pricing, new partner policies, a general CMS, or migrations unless needed and specifically justified. Add focused tests plus existing full PostgreSQL suite; review changes for privacy and publication leakage. Open a **draft PR** with screenshots at desktop and mobile breakpoints and a checklist of acceptance results. Do not merge or deploy.

## 11. Project boundaries and real November planning
The HQ record contains proposed November programs and confirmed *target date ranges* that are not by themselves evidence of public publication: street-food market circa Nov 19, stand-up around Nov 21, screenings Nov 22–23, Design Week Nov 25–29, 2026. Individual commitments/partners, poster release, capacity, prices and rights need separate explicit confirmation and publication through the source system. Habaq Media editorial independence and separate Habaq/Hub financial responsibility continue to apply.

## 12. Decision gate before live work
Review/approve Sprint A scope and public brand tone, then implement on a feature branch. Do not present this proposed layout or data model as an approved institutional commitment. No production changes are included in this document.
