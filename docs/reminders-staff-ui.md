# Reminders — Arabic staff workspace (Phase 4b)

The new page lives at `/staff/operations/reminders/` (`staff_reminders`).
This is an additive, server-rendered RTL page based on the existing
Business Day and operational-task styles, without a JavaScript dependency.
It remains hidden from the task navigation until the existing engine flag
`REMINDERS_ENGINE_ENABLED` is enabled.

## Staff actions and authorization
- Administrators or the person currently assigned **operations Active Duty**
  can create an independent reminder with title, optional details, expiry,
  once/interval/daily/weekly recurrence, duty responsibility and optional
  designated staff member. The backend validates the date and repeat pattern.
- A generic cashier account role does not, by itself, grant reminder
  management. Non-managers see only schedules that the
  current Active Duty resolver would actually route to them, including
  accepted handovers and the operations-lead fallback.
- For a standalone active reminder, the authorized holder can mark it
  completed or snooze it for 15/30/60/240 minutes. Managers can also cancel.
  All mutations use the existing audited reminder service.
- Linked source-task reminders show **فتح المهمة** instead of competing
  complete/snooze controls. The source Task remains authoritative.
- The history filter displays completed/cancelled reminders updated within
  30 days. A one-time reminder that already fired remains active until
  completed; reading the in-app alert does not count as completion.
- An unauthenticated user, inactive staff account or Internet Provider
  cannot use the page. POST requests still enforce CSRF, effective staff
  capability and record-level duty/role checks.
- If the engine is disabled, the direct page URL shows an explanation,
  and **all POST mutations are rejected**. The task workspace link stays
  hidden. No reminder events or network deliveries are created by a page view.

## Operator checks
1. Deploy is separate from PR merge. Ensure the live VPS revision, DB
   migrations, backup and daily tick service match the approved build.
2. Confirm owner sees the creation form and ordinary staff do not.
3. Test an actual service-duty holder, then an accepted Tamer→Salwa handover.
   The former holder must lose editing access immediately.
4. Confirm snooze, complete and cancel through the server-rendered form.
5. Confirm the engine-off state and the lack of browser/lock-screen pushes
   for operational reminders.
6. Confirm completing a task-linked reminder requires visiting the original
   task and cannot bypass task authorization.

## Deferred
An interactive urgency/acknowledgement inbox, per-station backup assignment,
multi-step escalation and customer help/bill request workflows remain separate
milestones. The page does not add another scheduler, payment mutation or
MikroTik setting.
