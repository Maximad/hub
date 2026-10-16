# Duty-aware staff notification rollout — Phase 2b

## Scope and default
`DUTY_NOTIFICATION_ROUTING_ENABLED=false` preserves legacy delivery until
explicit activation. The flag does **not** switch on any other subsystem.

Only **new** `new_order`, `delivery_order_created`, `new_prep_item`, and
`prep_item_ready` notifications produced by `create_notification` while
an actual Business Day is open are stamped with that day's routing date.
Historical notifications are never migrated or retroactively reclassified.
Existing private notices, guest Wi-Fi PIN, staff codes, payments and manager
approvals are outside this change.

## Live audience
- New orders / delivery / ready pickup: checked-in service duty holder if
  effectively authorized for the event; otherwise cashier duty holder.
- Preparation: checked-in kitchen or bar duty holder based on the station.
- Missing/ineligible holder: active authorized managers/admins are the visible
  fallback, including their Web Push if enabled. This is *not* a silent
  broadcast to all rostered employees.
- No available authorized manager: warn and retain the legacy recipient set
  rather than silently drop the alert. This case is a critical readiness gap.
- Effective capability checks run at enqueue, device send, and inbox lookup;
  no user gains an authorization to perform an operational action.
- Only explicit per-user read receipts are mutated by the existing mark-read
  endpoint. The new policy does not modify shared read flags.

The in-app audience and Web Push audience use the same resolver. Duty coverage
is evaluated at read/send time: an accepted handover changes whose inbox
shows **still-unread** role broadcasts. A pending handover does not.
Already-sent OS notifications cannot be recalled; an old pending push is
skipped on worker revalidation when the holder changes. We do *not* re-push
all old notifications to the new holder because the source order's completion
state must first be consulted to avoid stale action reminders. The underlying
order/task workspaces remain authoritative.

## Business Day and rollback
- If no Business Day is open, the event is not stamped and legacy routing
  remains intact; do not enable the flag without daily roster coverage.
- When the flag is switched off, stamped events temporarily display via the
  legacy audience again. This is the intentional operational rollback.
- Previously stamped events from prior Business Days continue to use their
  historical audience. Avoid re-broadcasting historical alerts as new pushes.
- No changes to the MikroTik, financial Shift, preparation transitions, guest
  Internet sessions, or daily code notification confidentiality.

## Rollout conditions
1. Full Django/PostgreSQL suite and migration-drift checks green.
2. Verify Business Day + confirmed check-in for service, cashier, bar and
   kitchen, then explicitly assign coverage on the Business Day screen.
3. Run diagnostics before enabling: ensure manager has a supported registered
   device; otherwise missing coverage becomes an in-app-only escalation.
4. Turn on `DUTY_NOTIFICATION_ROUTING_ENABLED` only after live approval and
   restart of Django web + notification worker using existing deploy process.
5. Test Tamer-to-Salwa accepted handover, new order, bar/kitchen prep,
   failed-subscription handling, permissions revoked mid-shift, phone
   foreground/background behavior and unassigned manager escalation.
6. Inspect unread and delivery logs, verify rollback by turning flag off.

## Follow-up work
Add an explicit unassigned operational queue and manager SLA/escalation view;
safe re-enqueue of genuinely outstanding work after handover; station-specific
backup coverage; actual on-duty reminders; service request acknowledgement and
two action buttons. This phase does not create another scheduler, duplicate
orders, or add uncontrolled notifications.
