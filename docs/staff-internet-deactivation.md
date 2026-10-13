# Staff Wi-Fi: account deactivation and self-service independence

## Rules
- Staff grants belong to Hub user accounts; customer visits, payments and
  provider revenue are never created by staff internet connections.
- A staff account with `is_active=False` is blocked immediately from fresh
  Django authorization. A post-commit hook revokes its private Internet grants,
  settles any recorded entitlement sessions, and queues a normal disconnect.
- The existing Internet worker reconciles disabled staff grants on its next
  lifecycle pass (configured every 60 seconds in production). This catches
  direct QuerySet.update(), imported changes and interrupted post-commit hooks.
- Deactivation revocation is idempotent. Reactivation does not reissue grants;
  managers must grant anew using existing permissions.
- Staff Wi-Fi availability no longer depends on the customer visit and
  customer internet self-service feature flags.

## Operational cautions
- Router operations are carried only by the existing network outbox against
  Hub-owned HotSpot resources. There is no RouterOS settings change or sync.
- A successful queue enqueue is not proof that the router disconnected the
  device. Failed disconnects remain in the existing retryable outbox, and
  operators must review unresolved failures.
- Current direct staff relay still has no verified per-device/online session
  accounting. Do not advertise configured minute or device caps as enforced.
- Do not infer actual connected staff devices from HotSpot relay-request counts.

## Read-only audit
`python manage.py reconcile_internet_lifecycle --dry-run --json`
now includes `inactive_staff_grants_to_revoke` (entitlement IDs only).
Normal reconciliation automatically revokes disabled-account grants.

## Verification
Exercise normal staff sign-in/relay with customer self-service disabled, user
deactivation through an ordinary Django save, bulk database deactivation caught
by the worker, repeat reconcilers, automatic audit/outbox state, and reactivation.
Verify existing grants for active employees and all commercial objects unchanged.
