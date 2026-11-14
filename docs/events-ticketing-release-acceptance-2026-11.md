# Hub Sweida event ticketing — launch acceptance runbook

**Scope:** Events, reservations, cashier receipts, ticket admission, CSV imports,
event covers and known-cost reporting. Owner: Hub Sweida operations/finance.
**Status:** Four-phase implementation merged to `main`. Engineering CI is not proof of production acceptance; verify the current combined commit before deploying.

## Release preconditions

1. Verify deployed container commit and required migrations
   (`reservations.0006–0013`, `events.0003–0009`) against the exact merged release.
   In particular, verify `events.0009_event_performances` and
   `reservations.0012_performance_links`: they create the original performance
   and attach legacy bookings/attendance requests. On a restored staging copy,
   verify those links and unchanged booking counts before any authorized rollout.
   Verify `reservations.0013_attendance_account` and its member-account dependency.
   Existing requests remain unclaimed: the migration must not associate accounts
   by phone. On synthetic staging records, explicitly claim a browser-owned request,
   verify access from another authenticated device, and reject another account or
   the old browser token alone. Verify revoked-device denial and own-order privacy.
   Record `python manage.py showmigrations events reservations --plan` and
   `python manage.py migrate --check` from the target environment; a historical
   migration range alone is not proof that its schema matches the release.
   Run full Django/PostgreSQL CI and `python manage.py check --deploy`.
2. Confirm a completed backup **and verified restore evidence**; rollback plan must
   preserve any real post-deploy transactions. Never reset production data.
3. Resolve the 21 November 2026 show schedule and number of performances before
   creating bookings. Use one Event with one independent EventPerformance per
   confirmed showtime; do not create duplicate Events for the same production.
   Prior screenshots recorded a 20:00 event, while discussion included 18:00
   and 21:00. Neither is an approved schedule.
4. Get a signed-off venue-safe capacity and independently checked exit/aisle
   arrangement. The prior entered value of 250 is **not** an approved capacity.
   Prior ticket tier allocations (60 rear + 25 near-stage = 85) require review.
5. Verify which Syrian-pound denomination the live database uses. Compare UI
   prices and real cash rates; never multiply prices automatically during import.
6. Ensure approved artwork and copy, public visibility, contact handling,
   consent, and at least two trained attendance reviewers. Public attendance
   requests are approvals, **not** payments or electronic ticket purchases.
7. Limit financial responsibilities to Hub Sweida. Do not transfer Habaq grants,
   other organizations' transactions, contact lists, or restricted records into
   Hub expense entries.

## Staff acceptance: synthetic records on staging

1. Open `/staff/events/`, create a **draft test event**, upload image at its
   staff artwork page, inspect responsive card, detail and public visibility.
   No test event should accidentally appear in the public event catalog.
2. Configure two ticket tiers. Make pending and confirmed reservations; set mixed
   ticket quantities equal to party size; challenge capacity/tier concurrency.
3. Edit a booking's name, phone, note and group size; reject oversell, foreign
   tier IDs, and size changes after a ticket invoice or posted receipt.
4. Issue ticket invoice once; retry the same request, assert no second Order.
   Confirm it uses hidden event-ticket SKUs with booked price snapshots and
   does **not** select any included drink or deduct beverage inventory.
5. Collect a partial amount through the authorized staff cashier screen.
   Verify a genuine posted `Payment`, balanced `PostingBatch`, CashMovement
   for cash, exact-order linkage, partial balance and idempotent retry.
6. Collect the remainder; verify `مدفوع بالكامل`. Try linking an unrelated
   event receipt; the system must refuse. Verify owner/admin and cashier
   permission boundaries.
7. Test real refund workflow only on **synthetic staging payments**; an
   authorized finance reversal must require evidence the refund has physically
   completed and post equal-and-opposite journal. It must not reopen an
   already used/expired ticket. Audit the new outstanding balance.
8. Scan/search guest name or phone on the staff event check-in desk. Admit
   only a confirmed reservation on its selected performance's Damascus-local
   date (not another performance's date). Partial group arrival must be
   idempotent and not open a HubVisit.
9. Import a **synthetic** UTF-8 CSV: preview, check names/phones/tiers,
   re-upload unchanged file to confirm. Duplicate file retry must create no
   additional records; mismatched file and over-capacity batches must fail
   atomically without logging private guest contacts.
10. Attach one already-approved Hub expense to a test event by amount.
    Check that it does **not** create or pay an expense. Over-allocations and
    external expenses must be rejected. Finance report shows verified posted
    receipts and known paid allocated costs, explicitly **not full net profit**.

## Live go/no-go

- Only publish/accept reservations for events with approved schedule and safety.
- Ensure roles and guest privacy: event staff need no broad finance or external
  project access. Never export guest names/phones into GitHub, Notion or logs.
- Use a small supervised first booking with a verified receipt. Compare Hub
  cashier, reservation status and independent cash/count records.
- Check `/staff/events/`, `/staff/reservations/`, public `/events/`,
  media URLs, Arabic RTL on small phones, daily close and audit trail.
- Explicitly distinguish booking nominal values, guaranteed unpaid, pending,
  posted/reversed payments, transferred funds and event cost allocations.
- Do not label an event **profitable** while costs, refunds, shared expenses and
  complimentary drink redemption are incomplete.
- If any finance posting, allocation, privacy, booking duplication or capacity
  check fails, stop rollout and use the verified rollback procedure.

## Not yet automated

- Venue fire/occupancy approval, actual safe capacity, staffing, performance
  schedule, cash reconciliation and contractual partner/artist settlement.
- Per-ticket drink entitlement/redemption and its stock/cost accounting.
- Complete P&L with overhead, staff compensation, refunds, taxes and external
  cost allocations; the events cost report explicitly labels a partial
  journal-backed cash difference, not net profit.
