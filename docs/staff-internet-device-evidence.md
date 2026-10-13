# Staff Wi-Fi — pseudonymous 30-day device evidence (observation only)

This Django-only milestone supplements the existing GET-only RouterOS HotSpot
poller. The worker samples observed active MAC addresses for each confirmed,
Hub-owned staff HotSpot entitlement. Canonical addresses are processed only in
memory and converted to a keyed SHA-256 HMAC, scoped to the internal staff
grant before persistence. No MAC, remote session ID, username, credential,
IP address or RouterOS raw response is saved in device-history rows.

Data are retained for a maximum of 30 days from the last successful
observation and pruned during subsequent observation cycles. Records include
only first/last observed timestamps and last active-login uptime (when
available). The manager-only dashboard may show "at least N distinct observed
network addresses in the past 30 days", not an enforced registered-device
count. An incomplete or failed active-session query never creates new
identities and never silently claims device compliance.

## Identity and accounting limitations

- RouterOS MAC is **not** a guaranteed permanent phone identity. Private /
  randomized Wi-Fi addresses, device resets and credential sharing can alter
  which network address appears under one grant.
- The HMAC key derives from Django SECRET_KEY; rotating that secret invalidates
  continuity of existing tokens. Historical rows are never automatically merged.
- A duplicate login on the same MAC counts as one observed network address.
- Uptime shown is the *current* active login duration. It is not a reliable
  cumulative billing, daily, or total-minute counter.
- Gaps caused by network downtime, polling delays or worker failure cannot be
  reconstructed from these snapshots. Do **not** enforce any minute cap from
  this record, nor infer that an unseen device never connected.
- No automatic revocation, access denial, quota enforcement, RouterOS device
  registration, or router configuration change is introduced.

## Device validation before enabling enforcement

Using a designated staff device, perform a reconnect with Wi-Fi MAC
randomization on/off, multi-device connections, and disconnections. Compare
time-stamped dashboard observations with what the device actually did.
Test a missed worker interval and RouterOS reboot. Confirm the policies for
shared staff accounts, address rotation, false positives and appeals.
Use the established secure production deployment with the MikroTik compose
overlay and verified CA mount for the **single consolidated rollout**.
