# MikroTik HotSpot runbook

Hub supports **RouterOS v7 HTTPS REST** only. Keep `MIKROTIK_ENABLED=false` until
the provider has prepared dedicated Hub resources. Prefer a private tunnel
between the VPS and router; never expose REST directly to the public Internet.

## Router-side boundary

The provider creates a least-privilege REST service account, a Hub-specific
HotSpot server, and pre-created user profiles. Hub may read system resources and
profiles and may create/update Hub HotSpot users and remove only their active
sessions. It must never change interfaces, bridges, DHCP, routes, firewall,
provider users, or unrelated sessions. Hub users carry
`hub-entitlement:<entitlement_id>`; a collision without that exact tag is refused.

## Configuration and TLS

Copy the `MIKROTIK_*` variables from `.env.example`. `MIKROTIK_BASE_URL` is an
HTTPS origin (an existing `/rest` suffix is accepted). TLS verification defaults
on. Set `MIKROTIK_CA_FILE` to the mounted private-CA bundle when applicable.
Store the Basic Auth password and the independently generated Fernet
`MIKROTIK_CREDENTIAL_KEY` only in the deployment secret store. The latter
encrypts per-entitlement HotSpot credentials at rest; rotation requires a
separate controlled re-encryption procedure. Map each Hub bandwidth profile's
optional `router_profile_name`, or configure `MIKROTIK_DEFAULT_PROFILE`.

## Customer portal before Internet login

The open SSID must let unauthenticated devices resolve and reach the Hub HTTPS
hostname (currently `hubsweida.jwtalenthouse.com`) through the HotSpot
walled garden. Permit the Hub host for `/wifi/`, `/menu/`, table menu and order
routes, `/static/`, and any Hub-hosted `/media/` used by the menu; the router
may authorize the host as a whole rather than individual paths. Keep the
existing HotSpot login origin reachable so the one-tap relay can POST its
credentials. Do not grant general Internet access by opening unrelated hosts.
One-tap login also requires a working HTTPS HotSpot login servlet configured as
`MIKROTIK_HOTSPOT_LOGIN_URL`; an HTTP-only CHAP login cannot use this relay.

From a freshly connected phone with no Internet authorization, verify both
paths: (1) tap **افتح المنيو واطلب**, view product images, submit a real test
order, and see its confirmation without starting Wi-Fi; (2) return to
`/wifi/`, tap **اتصل مجاناً**, let the router authorize the device, and confirm
the browser lands on the menu. Repeat on Android and iOS captive browsers.
If `/wifi/` fails with `ERR_CONNECTION_CLOSED`, fix the DNS, TLS, reverse proxy
and walled-garden reachability before evaluating the Django page.

## Rollout

Deploy and migrate first with integration disabled:

```sh
MIKROTIK_ENABLED=false python manage.py migrate
MIKROTIK_ENABLED=false python manage.py check
MIKROTIK_ENABLED=false python manage.py mikrotik_healthcheck
```

After private connectivity, certificates, account, server and profiles exist:

```sh
python manage.py mikrotik_healthcheck
python manage.py mikrotik_canary <ENTITLEMENT_ID>
python manage.py mikrotik_canary <ENTITLEMENT_ID> --execute
```

The first canary is read-only; `--execute` is mandatory and affects one
entitlement. To roll back immediately, set `MIKROTIK_ENABLED=false`, restart the
application, and continue with the Manual backend. Disabling Hub does not mutate
the router.

## Troubleshooting

Configuration errors indicate missing HTTPS URL, credentials, HotSpot server,
profile, CA, or encryption key. Authentication errors require the provider to
check only the dedicated service account. Connection errors require checking the
private tunnel, DNS, firewall reachability and certificate chain. Provisioning
errors preserve sales/payment state and put only network state into error. Never
paste Authorization headers, cookies, secrets, or full RouterOS responses into
logs or tickets.
