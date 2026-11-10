# Charcoal program workspace — Phase 3

**Owner:** Hub Sweida product / operations.  
**Scope:** Staff Events index, Event Details, Event Creation and Reservations index.  
**Status:** staged UI implementation; no production deployment claimed.

## Design implementation

Reuses the existing `static/css/staff_charcoal.css` palette and the staff navigation introduced by PR #403. All four templates opt in through the `staff-charcoal-program` body class; no duplicate framework or JS is introduced.

- Charcoal canvas `#15181C`, graphite surfaces `#1E2329`, raised cards `#262C33`.
- Live event media remains the hero; fallback uses architectural graphite/amber treatment. No invented event or image.
- Editorial header and restrained geometric motif; teal is reserved for main actions and interactive links.
- Semantic distinct statuses for published/cancelled/confirmed and readable reservation check-in controls.
- Form controls, selectors and inputs use dark surfaces; keyboard outline and reduced-motion styling included.
- Existing mobile event-card breakpoints and staff responsive layout retained.

## Protected behavior and data

No changes to events/reservations views, scheduling/recurrence, payment flow, publication rules, event image selection, per-performance capacities, booking constraints, check-in, security checks, permissions, database schemas or routes. Existing Habaq Media editorial independence remains outside this Hub commercial feature.

The displayed number of bookings is not assumed to equal paid tickets or approved venue capacity.

## Verification gate

1. `python manage.py check`
2. `python manage.py test core.tests.test_staff_charcoal_program core.tests.test_staff_events_workspace core.tests.test_staff_charcoal core.tests.test_staff_charcoal_pos`
3. Full PostgreSQL CI and `collectstatic`
4. Browser screenshot QA at 360, 390, 430, 768, 1280, 1440 px; Arabic RTL, wide/tall posters, missing posters, long event names and empty lists
5. Keyboard focus, hover and contrast for links, dark fields, error states and status badges
6. Staff event detail: show times, capacity, ticket breakdown, reservations, check-in controls and schedule forms
7. Confirm public site, ticket and other admin pages stay unchanged; confirm actual production revision before announcing a rollout

## Remaining after Phase 3

- Staff event check-in and event booking/schedule editing specialist pages (visual-only when proven safe)
- Customer homepage, event discovery and menu, separately scoped
- Admin navigation plus inventory, cashier, finance and report density improvements
- Visual regression QA and consolidation of legacy CSS once parity is verified

**Do not conflate a merge with a live deployment.**
