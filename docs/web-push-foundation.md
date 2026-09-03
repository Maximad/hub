# Staff Web Push

## Scope

Hub uses the existing `NotificationEvent`, `NotificationRecipient`,
`NotificationPreference`, and `NotificationLog` records as the notification
source of truth. Web Push is an additional delivery channel, not a second
notification system.

The current implementation includes:

- provider-independent push transport boundary;
- authenticated browser subscription registration;
- staff PWA manifest and root-scoped service worker;
- durable push queue stored in `NotificationLog`;
- separate `notification-worker` production process;
- role expansion, user preference filtering, dedupe, preparation aggregation,
  retry/backoff, and permanent subscription revocation;
- foreground/background duplicate suppression;
- a read-only `push_readiness` operational audit.

Push remains disabled by default and can be deployed inert before rollout.

## Security boundaries

- VAPID private material is server-only and must never enter Git, templates, API
  responses, client JavaScript, logs, or error messages.
- Browser subscription endpoints and encryption keys are delivery credentials.
  Administrative surfaces identify subscriptions by digest/device label rather
  than exposing those credentials.
- Push payload links are restricted to the authenticated `/staff/` area.
- Subscription registration requires an authenticated staff session, CSRF, a
  bounded JSON request, and a trusted browser push-service hostname.
- The service worker does not intercept fetches or cache authenticated pages.
- Lock-screen payloads are intentionally generic. They do not include customer
  phone numbers, addresses, payment data, private notes, or product notes.
- Provider acceptance means only that the browser push provider accepted the
  request. It is not proof that a person saw the notification.
- `NotificationRecipient.read_at` remains the human acknowledgement signal.
- Queue/provider failures are isolated from order/request transactions.

## Initial push routing matrix

| Event | Push audience | Push |
| --- | --- | --- |
| New order | Admin, cashier, service/waiter | Yes |
| New preparation items | Relevant station + admin | Yes, grouped per order/station |
| Preparation item ready | Service/waiter + admin | Yes |
| Manager approval needed | Admin | Yes |
| Delivery order created | Admin, cashier, service/waiter | Yes |
| Payment pending | Cashier | No initially |
| Day closed | Admin, cashier | No initially |

The current account model uses the role name `waiter`; notification routing maps
the historical `service` audience to that role explicitly.

## Durable delivery behavior

`NotificationLog` rows with the browser channel act as the durable queue.
Delivery records are created only after the notification transaction commits.
The delivery worker then re-checks, immediately before sending:

- event active/expiry state;
- current user active state and role targeting;
- current user notification preferences;
- browser-notification opt-in;
- subscription active/revoked/permission state.

The worker claims records using database row locks plus a short lease so multiple
worker processes cannot normally send the same row concurrently. Temporary
provider failures use bounded exponential backoff. Permanent `404/410`
subscription failures revoke the subscription. Provider exception text is not
stored because browser libraries can include credentials in exception strings.

New preparation-item notifications use a dedupe key based on order + preparation
station. One order therefore produces at most one preparation push per device
for a station, while the payload reports the grouped item count.

## Foreground noise control

Background push and the existing five-second polling channel intentionally stay
separate, but they coordinate in the browser:

- if a `/staff/` window is visible, the service worker does not create an OS
  notification; it posts a `hub-push` message to the visible client instead;
- the visible page immediately polls the existing notification endpoint, updates
  the bell and plays the configured sound once;
- polling does not create a second browser notification while a background push
  subscription is active;
- when a hidden staff page becomes visible again it polls immediately instead of
  waiting for the next five-second interval;
- if no staff window is visible, the service worker shows the normal OS push
  notification and clicking it focuses/navigates an authenticated staff window.

This keeps polling/audio as the foreground fallback while avoiding two OS alerts
for the same operational event.

## Configuration

Push is inert unless all of the following are supplied at runtime:

```env
PUSH_NOTIFICATIONS_ENABLED=true
PUSH_PROVIDER=webpush
VAPID_PUBLIC_KEY=<public key>
VAPID_PRIVATE_KEY=<private key>
VAPID_SUBJECT=mailto:<operational contact>
PUSH_HTTP_TIMEOUT_SECONDS=10
PUSH_ENDPOINT_ALLOWED_HOSTS=fcm.googleapis.com,updates.push.services.mozilla.com,push.services.mozilla.com,web.push.apple.com,.notify.windows.com
```

Django system checks fail when push is enabled with an unsupported provider,
missing VAPID values, an invalid subject URI, identical public/private keys, or
an empty endpoint allowlist.

## Production process

Production Compose runs:

```text
web
internet-worker
notification-worker
```

The notification worker command is:

```bash
python manage.py run_notification_worker --interval 5 --limit 50
```

With `PUSH_NOTIFICATIONS_ENABLED=false`, the worker stays healthy but does not
claim or send queued deliveries.

## Readiness and monitoring

Use the read-only command:

```bash
python manage.py push_readiness
python manage.py push_readiness --json
```

When push is disabled it reports a clean inert state. When enabled it verifies,
without printing VAPID or subscription credentials:

- provider/VAPID/subject/allowed-host configuration;
- count of active granted Web Push devices;
- pending browser delivery count and deliveries overdue by more than ten minutes;
- failed browser deliveries during the last 24 hours.

A missing test device, stale queue, or recent provider failures are warnings.
Invalid enabled configuration is a failure. CI runs the command with push
disabled so deployment remains credential-free.

## Remaining rollout

Before broad activation:

1. deploy this code with push disabled;
2. generate/configure a real VAPID key pair outside Git;
3. run `push_readiness --json` and confirm configuration is valid;
4. enable one modern Android admin phone and test manager-approval push with the
   app visible, backgrounded, and closed;
5. test an iPhone installed to the Home Screen;
6. add a kitchen device and confirm grouped preparation pushes;
7. expand new-order routing to cashier/service devices;
8. monitor stale queue, provider failures, duplicate alerts, and acknowledgement
   times before broader rollout.

The Android 4.4.2 POS tablet remains on polling/audio fallback and is not a
supported Web Push device.
