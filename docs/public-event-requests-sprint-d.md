# Sprint D1 — event attendance requests with staff review

Scope selected by the owner: **event attendance requests, staff approval, no guaranteed seat or online payment before approval**. This increment adds intake and operational confirmation only. It does not add ticket purchases, deposits, online checkout, refunds, program enrolment, room booking or membership sales. Nothing is deployed or opened to real submissions by these migrations.

## Enablement and routes

The global `PUBLIC_HOMEPAGE_ENABLED` release gate still defaults to false. Each event additionally requires all of:

- `status=published`, `is_public=True` and a future start.
- Explicit `accepts_attendance_requests=True`, default **false** for new and existing events.
- A configured, positive capacity; model validation and a database check enforce this for opted-in events.

Superusers opt in through the existing Event admin after reviewing dates/capacity. Existing staff creation/import behavior retains the false default. Eligible event details show an **أرسل طلب حضور** CTA and explicit wording that a request does not hold a seat or buy a ticket.

| Route | Purpose |
| --- | --- |
| `/events/<uuid>/request/` | GET form, CSRF-protected POST submission |
| `/attendance/<uuid>/` | Browser-owned GET status, POST withdrawal while pending |
| `/staff/event-requests/` | Staff review inbox with filters and 25-row pages |
| `/staff/event-requests/<uuid>/` | Staff review detail and POST approval/rejection |

The staff inbox is linked from the existing reservation list. It uses the existing `reservations` capability, including the same permission policy as staff reservation operations. No new public list, attendee directory, contact lookup or staff permission grant is introduced.

## Requests do not hold seats

`EventAttendanceRequest` is separate from `Reservation`. Submission stores the requested name, normalized unverified contact number, party size and public event title/UUID snapshot. It creates no reservation, visit, member, subscription, order or payment. Pending requests therefore never enter the established reservation capacity total. Capacity can already be full while a request awaits review: submission is an expression of attendance interest, not an availability promise.

One pending/approved request per event/browser is enforced with a partial unique constraint. An existing active request is returned without changing its party/contact details; pending withdrawal then a fresh form is the way to change them. A client UUID makes exact retries idempotent; the same key with changed data is rejected. A key from another browser never grants access. This is browser continuity, not verified person identity: different devices or cleared cookies can represent the same person, and staff must check for duplicates and verify contact before approving.

## Staff review and reservation authority

Approval locks the request, rechecks current event publication/opt-in/start, and calls the existing transactional `create_reservation` service with status **confirmed**, requested party size, creator=reviewing staff, and deposit=0. Its event row lock and existing capacity policy remain authoritative, including existing pending/confirmed staff reservations. Concurrent approvals cannot overbook. Failure leaves the request pending and rolls back any attempted reservation/audit changes.

Approval retries return the existing decision and never create a second reservation, including if its linked reservation was later cancelled or deleted. Rejecting requires an internal reason. Review status, actor and timestamp are retained, with append-only `AuditEvent` entries for submission, withdrawal and staff decisions. Internal review reasons and actor identity are never returned on the public receipt.

The visitor can withdraw only a pending request. Approval versus withdrawal is serialized on the request row. After approval, changes/cancellation/check-in use the existing staff reservation workflow and its rules, including open-visit restrictions. The receipt derives the current result from the linked reservation, so cancellation, completion, no-show or a missing link cannot continue to look like a guaranteed active booking. A missing reservation asks the visitor to verify status with staff; it never silently creates a replacement.

Confirmation here is a reservation decision, **not a paid ticket or receipt**. Any later commercial arrangement remains in existing staff workflows; no event-specific payment or refund policy is inferred. Disabling event intake or withdrawing its public listing prevents further approval, but existing records remain available for staff disposition. Pending requests do not auto-expire or notify guests: staff reject them or visitors withdraw them. No email/SMS/push messages are sent by this flow.

## Browser ownership and privacy

Successful submission stores a high-entropy ownership nonce in the existing server-side Django session. Only its SHA-256 digest is persisted on the request. The public UUID is a locator, not an access credential: the receipt requires the submitting browser's matching session. The nonce/digest and raw contact values never appear in receipt URLs or public presentation data. Receipts show only the submitted public event title, party size and safe status wording; contact information remains staff-only.

Lost sessions/devices cannot recover a request by supplying a phone number. Staff handle contact verification through their existing operational channel. Public receipt responses are no-store, vary by session cookie through SessionMiddleware, and are marked `noindex, nofollow`. This does not revoke already downloaded pages.

Forms require CSRF, validate name/contact/party inputs, normalize Arabic/Persian digits, and include a honeypot. They do not verify phone ownership or attach a request to a member account. A valid unapproved request cannot access account, payment, internet or member data.

## Submission limits

Current development defaults in `config/settings.py`:

- `PUBLIC_ATTENDANCE_MAX_PARTY_SIZE = 10` (also cannot exceed the event's configured capacity).
- `PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT = 5` across events and request states.
- `PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT = 60` across browsers.

Database transaction-scoped advisory locks serialize the counters across concurrent submissions. Exact retries return before consuming a new submission; withdrawal/rejection does not clear recent history. Rate denials return 429. The IP counter stores only a keyed digest of `REMOTE_ADDR`, never the raw IP or untrusted forwarded headers. Shared proxies/NAT can share this bucket; review these configurable development defaults and trusted-client-IP infrastructure before a separate release. There is no claim of verified-person rate limiting or distributed-bot immunity.

## Reliability follow-up

The attendance flow now checks the event start deadline again **after acquiring the event lock** for both submission and approval. A query whose timestamp was captured before waiting cannot create a late request or reservation when the event has started during the wait.

Retry keys are serialized transactionally in addition to the existing browser/IP counters. Concurrent use of the same key from unrelated browsers/events produces one request and clean ownership denials, rather than late uniqueness/database errors. Existing browser-owned pending/approved requests are resolved before rate counters, including when a newly opened form generates a fresh retry key. This lookup changes no contact/party data and creates neither an audit event nor a reservation. New requests for another event/browser still face the configured submission limits.

These cases were reproduced against the original implementation with real PostgreSQL concurrency and controlled deadline progression. Run the full baseline with the documented fresh-database command: transaction tests can flush data-migration seeds, so reusing their database with `--keepdb` can leave unrelated default policies/access profiles absent. The follow-up tests also exercise the public form redirect at the submission limit. No schema, payment, access-policy, or release-setting change is included.

## Validation

```bash
python manage.py migrate --noinput
scripts/test-full.sh reservations.test_public_attendance reservations.test_attendance_reliability --noinput
PUBLIC_HOMEPAGE_ENABLED=true scripts/test-full.sh --noinput
python manage.py collectstatic --noinput
PYTHONPATH=. PUBLIC_HOMEPAGE_PREVIEW_DIR=/tmp/hub-sprint-d-preview python scripts/check-public-homepage.py
```

Tests cover opt-in/private/closed events, no GET writes, no seat/payment creation on intake, CSRF and input checks, ownership isolation, retries and changed payloads, rate controls, pending withdrawal, staff permissions, internal-reason exclusion, canonical reservation status changes, atomic rollback and PostgreSQL concurrency for duplicate submissions, competing approvals and approval versus withdrawal. Prior public discovery/editorial and existing application regressions remain included.

Screenshots use **synthetic layout-only data**, not real attendee information or event announcements:

- Form: [desktop](screenshots/public-event-requests-sprint-d/attendance-form-1280.png), [mobile](screenshots/public-event-requests-sprint-d/attendance-form-390.png).
- Pending status: [mobile](screenshots/public-event-requests-sprint-d/attendance-pending-390.png).
- Approved status: [mobile](screenshots/public-event-requests-sprint-d/attendance-approved-390.png).
- Rejected status: [mobile](screenshots/public-event-requests-sprint-d/attendance-rejected-390.png).
- Staff review: [mobile](screenshots/public-event-requests-sprint-d/attendance-staff-390.png).

Production migrations, contact/capacity policy review, staging inspection, public release enablement and per-event opt-in remain separate operations. No live event is opened to requests by this work.
