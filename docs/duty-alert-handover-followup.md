# Unassigned duty alerts and safe handover requeue (dependent on PR #296)

## Why this layer is separate
Business Day and Active Duty remain the source of **who** is responsible.
Order/OrderItem remain the source of whether work is **still outstanding**.
This module is only a read-only operational projection plus a deduplicated
Web Push adapter. There is no new task database, alert timer, or status
field: reminders will use their own reusable engine in a later phase.

## Manager queue
When `DUTY_NOTIFICATION_ROUTING_ENABLED=true`, the existing Business Day
screen gains "تنبيهات تحتاج تغطية". Eligible admin/owner accounts see a
bounded list of current-day, unresolved, uncovered order/preparation alerts,
filtered by their effective capability. They can open the existing source
screen or navigate to duty assignments.

Displaying the queue **never** accepts orders, marks notices read, generates
new tasks, or exposes customer contact data. It is not shown to providers
or ordinary staff. An alert leaves the queue when source work progresses,
its duty slot is covered, it expires, or the Business Day ends.

## Handover
After an accepted handover or manager reassignment, an on-commit callback
finds only current-day alerts whose source is still actionable:
- new order: Order.status == new
- new delivery: delivery_status == new
- new prep item: prep_status in new/sent/pending (not accepted/preparing)
- ready item: prep_status == ready (not served/cancelled)

It calls the existing push queue, respecting subscription preferences and
per-device idempotency. The worker rechecks actual duty eligibility before
sending. No new NotificationEvent is synthesized and an old delivered push
cannot be recalled. Pending handovers never cause a requeue.

If the flag is disabled, the additional queue and requeue are inert and
existing role-based delivery continues unchanged. Previously stamped duty
alerts from expired Business Days are excluded from ordinary inboxes and
will not fan out to legacy recipients when a delayed worker finally runs.

## Remaining operational requirements
A real event-based reminder/escalation SLA, acknowledgements, backup duty,
uncovered work without browser connectivity, manual overrides, and
source-specific task assignment remain separate. No completion is inferred
from a notification read receipt. No sensitive codes should be included in
Web Push or this manager queue.

## Verification
Full CI, migration-drift, alert queue permissions, event-source transitions,
pre- versus post-handover recipients, idempotent duplicate handover retries,
closed-day suppression, default-off rollback, PR #293 guest PIN and PR #294
privacy regressions. Actual deployment/activation still requires separate
staff-device smoke testing.
