# Staff internet sampled continuity — non-authoritative evidence

## Objective

Persist a limited, append-only record of *adjacent observed connection
continuity*, per pseudonymous grant-scoped network address. This supports
reviewing missing data, reconnects and periods that were actually sampled.
It is deliberately not a billable usage total or authoritative minutes ledger.

The implementation requires two consecutive complete active-login observations
for one grant. For a specific observed network address, uptime must increase
approximately with wall-clock elapsed seconds, with a 5-second tolerance.
Only positive deltas, in intervals of at most 120 seconds, are accepted.
An accepted record credits the lesser of elapsed time or uptime delta.

A failed or incomplete observation, an unavailable MAC or uptime, an interval
longer than 120 seconds, a backwards/unchanged uptime, a router restart, and
duplicate/out-of-order timestamps never create a sampled continuity interval.
A new observation can resume after another consistent adjacent pair.

## Storage and limits

- Append-only StaffInternetObservedInterval records reference grant-scoped,
  keyed StaffInternetDeviceEvidence. Fields: observation start/end and seconds.
- No MAC, IP, RouterOS session ID, staff credential or RouterOS raw response
  is recorded. The 30-day retention ceiling also applies to intervals.
- A grant-row lock serializes writes; an old remote observation cannot
  overwrite a newer snapshot or double-count an interval.
- The management page reports sampled *device-minutes* over the previous
  24 hours when evidence exists. Multiple concurrent devices are summed,
  so it is not calendar elapsed time.
- Zero samples means **unknown / insufficient evidence**, not verified zero use.
- Device tokens may change with randomized Wi-Fi addresses or secret rotation.
- The comparison is conservative but still an inference, not independent
  proof of connection continuity, human/device identity or completed billing.

## Unresolved — explicitly out of scope

- Per-device and per-day authoritative usage accounting during poll outages.
- Correct billing or minute deductions across RouterOS restart/reconnections.
- Enforcing daily / total minutes, device registration or concurrency caps.
- Remediating existing entitlement grant settings and known staff devices.
- Any MikroTik configuration changes.

The read-only RouterOS poll path is unchanged. No automatic disconnects,
payments or customer orders are introduced. Complete real-device verification
after the consolidated release before claiming usage accuracy or quota support.
