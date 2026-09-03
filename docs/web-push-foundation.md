# Staff Web Push foundation

## Scope

This foundation adds a provider boundary and durable data structures for true
background staff notifications. It does not register browsers, enqueue delivery
jobs, start a worker, or send production notifications. The feature remains
disabled by default.

The existing `NotificationEvent` and `NotificationRecipient` records remain the
source of truth. Browser push will be an additional delivery channel recorded in
`NotificationLog`, not a parallel notification system.

## Security boundaries

- VAPID private material is server-only and must never enter Git, templates, API
  responses, client JavaScript, logs, or error messages.
- Browser subscription endpoints and encryption keys are delivery credentials.
  The technical admin view intentionally hides them.
- Push payload links are restricted to the authenticated `/staff/` area.
- Provider acceptance will mean only that the push service accepted the request;
  it must not be presented as proof that a person saw the notification.
- `NotificationRecipient.read_at` remains the acknowledgement signal.

## Configuration

Push is inert unless all of the following are supplied at runtime:

```env
PUSH_NOTIFICATIONS_ENABLED=true
PUSH_PROVIDER=webpush
VAPID_PUBLIC_KEY=<public key>
VAPID_PRIVATE_KEY=<private key>
VAPID_SUBJECT=mailto:<operational contact>
PUSH_HTTP_TIMEOUT_SECONDS=10
```

Django system checks fail when push is enabled with an unsupported provider,
missing VAPID values, an invalid subject URI, or identical public/private keys.

## Next implementation phase

The next phase should add authenticated subscription registration, a root-scoped
service worker and staff PWA manifest. Delivery remains off until a database-backed
worker, retry policy, recipient expansion, preference filtering, grouping, and
staging-device tests are complete.
