# Staff Wi-Fi — verified online observation (phase 1)

## Scope
On each production internet-worker lifecycle pass (normally every 60 s), Hub reads
at most 20 eligible active staff grants. It uses TLS-verified RouterOS REST
GET operations only. Oldest last-checked grant is polled first for fair rotation.

The query verifies the Hub ownership marker on the provisioned user before
reading `/ip/hotspot/active`. Unexpected RouterOS identities/results are never
treated as verified online counts.

Staff managers see the count of active HotSpot logins and, if complete MAC
metadata is returned, distinct active-device count. We store neither MAC
addresses, remote session IDs, nor decrypted credentials in the database.
The snapshot has a timestamp; data older than 3 minutes or from a failed
probe is presented as **unknown**, never as confirmed zero.

## Limitations (deliberate)
- Observed active logins are not proof of browser-initiated login requests.
- Distinct MAC counts are observational only, not registered-device history.
- RouterOS uptime may change during reconnection. This batch does not
  accumulate billable/daily/total staff usage or enforce device/time quotas.
- The portal labels configured device counts as requested, not enforced.
- Existing staff entitlements remain unchanged; no RouterOS profiles, settings,
  firewall, DNS, HotSpot resources or rates are configured by polling.
- A failed read does not clear active entitlements or trigger disconnect.
- The poller does not issue any POST/PUT/PATCH/DELETE RouterOS operations.

## After consolidated deployment
1. Verify the MikroTik CA mount and TLS healthcheck.
2. Wait two normal worker lifecycle cycles.
3. Check the internal staff grant dashboard and confirm recent snapshots.
4. On a designated staff phone: compare one real HotSpot connection and
   disconnection against the snapshot, allowing for worker polling delay.
5. Test multi-device behavior explicitly before considering enforcement.
6. Never infer an online device from 46 prior staff relay requests.
