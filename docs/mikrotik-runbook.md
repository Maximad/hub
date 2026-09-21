# MikroTik HotSpot runbook

Hub uses an existing RouterOS v7 HotSpot through HTTPS REST. The current router
setup is a fixed external dependency. Django may inspect the configured resources
and perform normal customer-session operations, but it must not configure or
repair the router.

## Current fixed resources

The current Hub mappings are:

- basic Internet → `hub-slow`
- fast Internet → `hub-full`
- HotSpot server → `hub-hotspot`

These names refer to resources already configured on MikroTik. Do not rewrite
their rate limits, comments, shared-user limits, enabled state, or other RouterOS
properties from Hub Suite.

## Router-side boundary

Django may:

- read basic RouterOS health/resource information;
- look up the configured HotSpot server and mapped user profiles;
- create/update Hub HotSpot users when provisioning customer access;
- remove only the active HotSpot sessions that belong to Hub-managed access.

Django must not:

- create/update HotSpot user profiles;
- create/update walled-garden rules;
- change interfaces, bridges, DHCP, DNS, routes, NAT, firewall, queues,
  certificates, HotSpot server settings, router accounts, permissions, or firmware;
- run router-writing setup during migrations, startup, deployment, readiness
  checks, settings saves, or tests.

Hub users continue to carry the existing Hub ownership marker used by the
session integration. Router configuration and customer-session operations are
separate concerns.

## Django profile records

`InternetBandwidthProfile` remains the application-side record used by packages,
sessions and policies. Its `router_profile_name` maps a Django profile to an
existing RouterOS profile.

Historical `download_limit_kbps` and `upload_limit_kbps` values are descriptive
application metadata only. They are not desired RouterOS configuration and must
not be pushed to the router or compared with RouterOS rate limits to decide
whether a mapping is usable.

A read-only mapping diagnostic may confirm that:

- `hub-hotspot` exists and is usable;
- the mapped profile name exists and is enabled.

It must not require rate/comment/shared-user equality and must not require a
Hub-managed walled-garden rule.

## Configuration and TLS

Copy the required `MIKROTIK_*` connection/session variables from `.env.example`.
`MIKROTIK_BASE_URL` is an HTTPS origin and TLS verification should remain
enabled. Secrets and the Fernet `MIKROTIK_CREDENTIAL_KEY` stay in deployment
secrets and must never be rendered in staff pages or logs.

A successful read-only check proves only that the configured read operation
succeeded. It does not prove broader write permissions and must not be presented
as such.

If a RouterOS operation fails, report the application step and a safe error
category. Do not expose credentials, Authorization headers, raw RouterOS
responses, or infer that broader service-account permissions are required
without verified evidence.

## Customer portal before Internet login

The customer flow is owned by Django, but reachability before HotSpot
authorization still depends on the unchanged network configuration.

Hub Suite may provide `/wifi/`, `/menu/`, ordering and the existing login relay.
Django cannot make an unreachable pre-login hostname reachable by itself. If a
designated-device flow test shows that the Hub hostname is unreachable before
login, record it as an external network limitation. Do not silently add router
configuration work to an application deployment.

## Application deployment

The Django deployment has no router synchronization step.

Normal application rollout remains:

1. create/verify the application/database backup required by the main deployment
   runbook;
2. deploy the reviewed Django code;
3. run migrations;
4. collect static files;
5. restart the application;
6. run Django/system smoke tests;
7. optionally run the read-only MikroTik health check;
8. test customer flows on a designated device against the unchanged network.

Never run profile synchronization, walled-garden synchronization, speed setup, or
other RouterOS configuration as part of deploy, startup, migrations or readiness
checks.

## Session operations

Normal service operations remain supported through the existing integration:
customer access provisioning, selecting an existing mapped profile, and session
termination.

These operations must run only when the customer/session workflow requires them.
They must never be executed merely to inspect settings or prove readiness.

## Troubleshooting

Keep these states separate:

- Django configuration completeness;
- last successful read-only router connection check;
- Internet worker freshness;
- provisioning/termination operation failures.

Display timestamps and unknown/stale states where appropriate. A failure in one
state must not be rewritten as a claim about another state.

If the staff page receives an old/direct request for the retired portal-sync
action, Django must reject it without making any RouterOS configuration call.
