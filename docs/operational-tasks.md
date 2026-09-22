# Hub operational tasks

The operational-task layer sits under the existing Business Day system. It turns known operational conditions into visible, assignable work without giving the automation authority to change the source system.

## Staff workspace

The staff surface is:

`/staff/operations/tasks/`

It shows the current Business Day, open/overdue/completed counts, task source, due time, assignment, and completion state.

Managers (`admin`, `cashier`, and superusers) can:

- synchronize system-generated tasks;
- create one-off tasks for the current Business Day;
- assign tasks to active Hub staff;
- create or disable recurring task templates;
- complete, reopen, or waive tasks.

A non-manager staff user can update a task only when it is assigned directly to that user or targets that user's role. Internet-provider accounts are excluded from this workspace.

Waiving a task requires a written reason. Completion and assignment changes are audited.

## Recurring routines

`OperationalTaskTemplate` stores reusable routines. A template can define:

- Arabic title and instructions;
- weekdays (`0=Monday` through `6=Sunday`); an empty weekday list means every Business Day;
- due time;
- normal/high priority;
- responsible staff role;
- whether the task is marked required;
- optional active date range.

At Business Day creation and on each automation tick, applicable templates materialize into one `BusinessDayTask` each. Stable fingerprints prevent duplicates.

A due time before the Business Day cutoff belongs to the next calendar date while remaining part of the same operating day. With the default 04:00 cutoff, a routine due at 02:00 on the operating day of September 22 is due at 02:00 on September 23.

## Event preparation

A published `Event` whose `starts_at` falls inside the Business Day window creates one high-priority required preparation task.

Default lead time: `BUSINESS_DAY_EVENT_PREP_LEAD_MINUTES=120`.

The task shows the event title, room when available, capacity when available, and links back to the event staff page.

The automation does **not** publish, cancel, edit, reschedule, or complete the event.

## Reservation preparation

A confirmed regular reservation whose actual start datetime falls inside the Business Day window creates one preparation task.

Default lead time: `BUSINESS_DAY_RESERVATION_PREP_LEAD_MINUTES=30`.

The calculation respects the operating-day cutoff. For example, a confirmed reservation at 01:30 on September 23 belongs to the September 22 Business Day when the cutoff is 04:00.

The automation does **not** confirm, cancel, check in, complete, reschedule, or alter the reservation.

## Low-stock follow-up

If one or more active `InventoryItem` rows are at or below their configured low-stock threshold, Hub creates one high-priority review task listing the affected items.

This is deliberately a review task only. It does **not**:

- increase or reduce inventory quantities;
- create a purchase;
- choose a supplier;
- receive stock;
- create an expense or financial posting.

When the low-stock condition disappears, a still-pending system-generated task is auto-waived with a system reason. If the condition later returns during the same Business Day, that system-auto-waived task reopens. A task that staff manually completed or waived is never silently reopened.

The same source-return behavior applies to other auto-waived generated tasks such as event/reservation preparation.

## Task status and closing

Task states are:

- `pending` — still open;
- `done` — completed by staff;
- `waived` — closed without execution, with a recorded reason.

The `required` flag is an operational attention marker in this release. Required tasks are included in anomaly reporting and the owner digest, but they are **not hidden financial/operational close blockers**. Existing `BusinessDayException` blockers remain the authority for whether a Business Day may close.

This distinction is intentional: staff can see that required work is unfinished without allowing the task system to silently alter the established close contract.

## Reminders and scheduler

The existing idempotent command also synchronizes and reminds tasks:

```bash
python manage.py business_day_tick
```

Each normal tick:

1. materializes/refreshes source-driven and recurring tasks;
2. auto-waives pending generated tasks whose source condition disappeared;
3. reminds the responsible person/role about overdue tasks, with manager fallback;
4. runs the existing daily-code and incomplete-opening reminders.

Task reminders are rate-limited by `BUSINESS_DAY_TASK_REMINDER_MINUTES` (default 60).

`--dry-run` reports task counts but does not create tasks, open a Business Day, or send reminders.

## Owner indicators

Business Day metrics now include:

- open task count;
- overdue task count;
- required-open task count.

Advisory anomaly indicators are raised for overdue tasks and required tasks that remain open. The end-of-day owner digest includes open/overdue task counts.

## Hard boundaries

Operational tasks never automatically:

- settle, discount, cancel, or create orders;
- close shifts or financial accounts;
- create or edit `DailyClose` records;
- modify events or reservations;
- change inventory quantities or create purchases;
- change MikroTik/RouterOS configuration.

Source systems remain authoritative. The task layer only observes them and coordinates human follow-up.
