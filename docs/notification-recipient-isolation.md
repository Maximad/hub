# Notification recipient correctness — Phase 1

## Scope
This additive milestone isolates notification visibility and read status, without
changing the production Web Push worker, staff roster, order mutations, or push
opt-in flow. It starts from main after merged operations PR #293.

## Recipient contract
- A `NotificationRecipient(user=<account>, role=<legacy-label>)` is **private
  to that user**. Its role is descriptive and must not expand visibility to
  other users with the same role, including managers/superusers.
- A `NotificationRecipient(user=None, role=<audience>)` is a broadcast.
  Legacy `service` maps to the current `waiter` account role. Managers
  retain access to broadcasts under the existing staff-inbox policy.
- `NotificationRecipient.is_read/read_at/delivered_at` are historical
  shared-state fields. New reads **do not update** them.
- `NotificationReadReceipt(notification_recipient, user, read_at)` stores
  personal acknowledgement of reading. One row per recipient/user, with
  a database uniqueness constraint; repeated read requests are idempotent.
- Existing shared `is_read=True` flags are respected, because the identities
  of historical readers cannot be reconstructed without inventing data.
- A recipient seen in a group for a user is unread only if both the legacy
  field is false and no matching per-user receipt exists.

## Guardrails
- A missing, unreadable or forged mark-read key changes nothing.
- Existing in-app guest Wi-Fi daily PIN events are delivered only to their
  explicit staff recipients and expire at the Business Day boundary.
  They remain excluded from the browser-push route. Never expose PINs
  on lock screens or external channels.
- Daily staff codes remain governed by the separate existing scrubber and
  staff operational workflow. No new sensitive data enters notifications.
- Actual operational *actions* keep their existing effective capability,
  transition, and financial authorization checks.
- The shared legacy role alias mapping is **not** an operational duty resolver
  and must not be used to grant capabilities.
- Web Push provider acceptance is not proof of device display or staff action.

## Rollout and follow-up
1. Run the full Django/PostgreSQL CI and migration-drift checks.
2. Apply the additive `core.0053` migration before enabling the new code.
3. On two logged-in cashier accounts, create a regular role broadcast, mark
   it read as one cashier and confirm it stays unread for the other.
4. Confirm handover and owner-digest notices do not appear in another
   same-role user's inbox; confirm waiter/service legacy aliases.
5. Check the daily guest PIN in-app, expiration, and absence from push logs.
6. Confirm push delivery and in-app visibility on the existing staff devices.

Later increments will implement the duty-aware role/capability resolver, task
ownership, direct contextual actions, smarter read semantics, a reusable reminder
service, and customer assistance response/escalation. They should extend
existing Business Day and task systems rather than create parallel worklists.

Historical `NotificationRecipient` rows and `NotificationLog` delivery
records are never rekeyed, deleted or bulk-mutated by this milestone.
