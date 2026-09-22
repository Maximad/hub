# Internet final verification

This checklist verifies the Hub/Django Internet experience against the existing MikroTik installation. It is not a router-configuration procedure.

## Hard boundary

Do not change MikroTik profiles, rates, HotSpot settings, walled garden, firewall/NAT, DNS/DHCP, bridges/interfaces, queues, certificates, users/permissions, firmware, or service-account privileges during this verification.

Existing mappings remain:

- basic Internet → `hub-slow`
- fast Internet → `hub-full`
- HotSpot server → `hub-hotspot`

A read-only health check confirms only that Hub can read the mapped resources. It does not prove broad write permissions.

## Automated contract

Before rollout, CI must pass the full PostgreSQL suite, including `core.tests.test_internet_launch_contract`.

The contract protects these launch guarantees:

1. Opening `/wifi/`, the menu, or Internet choices does not itself start or bill Internet.
2. Menu/order access does not require the venue PIN or an Internet purchase.
3. Basic Internet still requires the venue PIN when policy requires it.
4. Basic allowance and accepted-order bonuses remain bounded by the daily complimentary cap.
5. Internal owner/team access uses entitlement limits but creates no Order, Payment, revenue share, or provider-visible commercial record.
6. Revoking internal access terminates active usage through the existing lifecycle.
7. Django exposes no RouterOS profile/walled-garden configuration writers and rejects the retired portal-sync action.
8. Provider visibility remains partner-scoped; internal grants stay private.
9. Hub session records and network-ready records are not presented as a live connected-device count.

## Staging verification

Use the normal Django staging deployment. Do not add router synchronization to deployment, migrations, startup, readiness, or tests.

Verify:

- `/wifi/` initially offers **Internet** and **menu/order** as independent choices.
- Opening the menu creates no Internet session.
- Opening Internet options without pressing a start/buy action creates no Internet session or charge.
- Basic Internet with a wrong venue PIN is rejected.
- Basic Internet with the valid venue PIN receives the configured bounded allowance.
- An accepted qualifying order applies the configured basic-Internet bonus without exceeding the daily cap.
- Fast access starts only after the customer explicitly chooses it.
- A session waiting for network authorization is shown as pending, never as connected.
- Ending fast access restores remaining basic allowance when one exists; otherwise the customer can still use the menu/order path.
- Internal owner/team access can be granted and revoked, respects device limits, and does not appear in paid sales/revenue-share reporting.
- Provider pages expose only that provider's scope. The default provider may see package-less MikroTik operational traffic, but not Hub internal grants.
- Staff/provider operational screens distinguish worker freshness, read-only router health, Hub session state, network-ready state, pending operations, and failed operations.

## Designated-device end-to-end check

Only after the Django build is authorized for rollout, use one designated customer device on the unchanged venue network:

1. Join the existing Hub Wi-Fi.
2. Open the Hub captive/Django entry page.
3. Open the menu without Internet authorization and confirm ordering remains available.
4. Return to Internet, enter the current venue PIN, and start basic access.
5. Confirm Hub shows pending until network authorization succeeds, then shows the connection as ready.
6. Start fast access through the normal customer flow and confirm the basic session is paused/ended as designed.
7. Stop/expire fast access and verify remaining basic allowance is restored when eligible.
8. Confirm staff operational views show the corresponding Hub/session operations without claiming they are a live RouterOS device census.

If the captive/pre-login hostname cannot reach Django before Django is loaded, record that as an external network limitation. Do not turn that observation into an automatic MikroTik change request.

## Production gate

Production deployment is allowed only after:

- the relevant PR is merged to `main`;
- `main` CI is green;
- staging/application verification passes;
- deployment is explicitly authorized.

Production verification remains application-side only. No MikroTik configuration step is part of the deployment.