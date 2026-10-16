# Reminders Engine — opt-in foundation

This module is separate from the Business Day task lifecycle, financial Shifts,
the Web Push worker and MikroTik. Existing Business Day task materialization and
the existing `business_day_tick` scheduler remain authoritative.

## Concepts and provenance
- `Reminder`: independent schedule with explicit status, due time, recurrence,
  expiry, target duty responsibility and optional assigned user.
- `Reminder.source_task`: optional one-to-one link to an existing BusinessDayTask.
  The underlying task remains the only source of completion, event/reservation
  state and inventory changes.
- `ReminderOccurrence`: append-only evidence of **one in-app alert generated**
  for a scheduled occurrence, with a unique database key
  `(reminder, scheduled_for)`. This is not proof of a user seeing or handling it.
- `NotificationEvent('operational_reminder')`: in-app-only, without Web Push
  or lock-screen payload. The event contains no reservation phone number, guest
  PIN, staff access code, or other task details.
- `Reminder.retry_after`: retry suppression when no eligible duty holder is
  available; it does not move `next_due_at` or alter a calendar recurrence.

## Schedules and repeat semantics
- Standalone reminders: once, fixed-minute interval (1 to 10080), daily at
  the local wall-clock time, or weekly at the local wall-clock day/time.
- Task-linked reminders: one schedule per existing task with a due time;
  repeat every configured overdue interval while that task remains pending.
- A missed run creates **at most one** notification and advances to the next
  future occurrence. There is no backlog replay or catch-up flood.
- Once-only reminders stop generating alerts when first delivered, but remain
  `active` until manually acknowledged/completed. Reading a notification
  is not acknowledgement.
- Due changes on pending source tasks update their associated schedule.
  Completed/waived source tasks automatically close their reminders.
  Reopened source tasks can resume their schedule.
- Source tasks without a due time, including the daily physical guest Wi-Fi
  PIN board task, do not receive an invented deadline.

## Recipients and safety
- Explicit staff targets are only eligible while currently holding the
  relevant responsibility; a past check-in alone is not sufficient.
- Otherwise the reminder follows the requested Active Duty responsibility;
  uncovered responsibilities fall back to the *on-duty* operations lead.
- When neither is assigned/eligible, no notification is emitted. The reminder
  remains pending, with a five-minute retry backoff. It never silently sends
  to every registered or rostered staff member.
- Existing direct notification privacy and individual read receipts apply.
- Staff users with the provider role are not eligible merely by membership.
- No Web Push route is installed for `operational_reminder`.
- Source task cancellation/completion is checked in the same transaction
  before creating a reminder occurrence; the event and schedule advance are
  atomic. Duplicate ticks do not duplicate an occurrence.

## Rollout and controls
- `REMINDERS_ENGINE_ENABLED=false` by default. With the flag off,
  `business_day_tick` continues to execute
  `send_overdue_task_reminders` unchanged.
- When enabled, `business_day_tick` first calls the *existing*
  `sync_business_day_tasks`, then `sync_task_reminders`, then `tick_reminders`;
  it does **not** call the legacy overdue reminder sender.
- Daily staff-code reminders, opening reminders and guest Wi-Fi PIN remain
  independently owned by their existing workflows, unchanged.
- Standalone schedules can be created/closed/snoozed through the audited
  `operations.reminders` service. Admin list views are available. A more
  accessible Arabic staff self-service UI is a following milestone, not included
  in this foundation.
- No new Redis, Celery, external service, cron job or worker is needed.
- Revert the feature immediately by setting
  `REMINDERS_ENGINE_ENABLED=false` and restarting the existing Django
  process/cron context using normal deployment practice.
- All data migrations are additive. Do not purge historical notifications or
  reminder occurrences.

## Verification before activation
1. Django/PostgreSQL full test suite and migration-drift checks.
2. Two actual staff accounts: active operations holder, service assignment,
   handover to another employee, and unassigned retry.
3. Generated event/reservation/inventory tasks: resolved sources stay stopped;
   manual tasks respect the original task status.
4. Repetition tests for daily, weekly, interval, expiry and DST boundaries.
5. Guest Wi-Fi PIN and staff-code privacy; no reminder on lock screen.
6. Production deploy revision, backup, and device acceptance checked
   separately before enabling the flag.

## Deferred
- A first-class reminders staff UI (create/snooze/complete in Arabic).
- Event-based urgency, second-stage escalation, actionable buttons and
  per-station backups.
- Guaranteed push-on-handover for previously pending reminders.
- Standalone reminders when there is no open Business Day.
