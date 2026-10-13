# Internal internet grant administration — permission hardening

Staff Wi-Fi grants are private operational entitlements. They are not commercial sales,
do not create customer visits or orders, and remain excluded from ISP partner reports.

## Permission boundary

- `internal_internet_manage` is a dedicated capability for creating, viewing and
  revoking private owner/team Internet grants.
- Every internal grant-management request also requires an active Hub owner/admin
  identity. A cashier, bartender, waiter or provider cannot become a grant manager
  solely through a manual capability override.
- Existing system `full-admin` profiles receive the new capability in migration 0006.
  Cashier and provider profiles do not. Manager profiles can be explicitly
  configured or denied with the normal capability system.
- Commercial `members/internet` access now goes through the authoritative
  access-profile/override check rather than raw legacy role matching. Commercial
  access and internal grant administration are independent.
- The Internet staff page hides the private grant shortcut for users without
  internal management authority. Server-side POST/GET authorization is decisive.

## Release and safety

- No network call is added to access checks. No existing grant is rotated,
  cancelled, reprovisioned or otherwise changed.
- There are no MikroTik profile, rate, HotSpot, firewall, DNS, certificate or
  network-account configuration changes.
- After rollout, verify `full-admin` profiles and assigned administrators,
  check the denied cashier paths, and confirm that the six current internal grants
  remain usable without any new order/payment.
- Staff connection session/device tracking, staff account deactivation handling,
  and customer fast-internet identification are follow-up milestones.
