# Staff Internet: observational limits and active-login uptime

This step adds two diagnostics to the existing read-only RouterOS HotSpot poller:

1. **Concurrent device comparison:** mark an observed excess only when fresh,
   complete distinct-MAC evidence exceeds configured max_concurrent_devices.
   Two logins with the same MAC count as one device. If any MAC is missing,
   the snapshot is stale or the query fails, the result is **unknown**.
2. **Longest currently active login duration:** parse RouterOS uptime in unit
   or clock format. Show whole minutes for the longest connection only when
   every active record provides parseable uptime. This does not measure daily,
   historical, billable, or total staff usage.

Snapshots store only counts, durations, timestamps and error codes, never raw
session IDs, MACs, credentials, or router error text.

The comparison is not automatic enforcement. It does not verify historical
registered-device limits, block customer/staff logins, or modify any MikroTik
profile, user, HotSpot server, speed, queues, DNS, firewall, or router settings.

## Prerequisites for enforcement

- Compare observed MAC and uptime fields with actual staff phones.
- Determine safe binding and identity for separate devices/reconnects.
- Build outage-safe durable usage accounting with explicit policy before using
  minute quotas; sampled active logins cannot establish complete history.
- Implement a separate, reviewed enforcement policy only after device validation.

This batch can be part of one consolidated Django production deployment.
