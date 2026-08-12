# Internet allowance authority

## Preserved product semantics

* `timed_session` snapshots and enforces the entitlement's per-session duration. It
  does not decrement a reusable total balance or automatically consume/end the
  entitlement; this is the behavior that existed before this change.
* `validity_pass` is bounded by its validity window and any configured session or
  daily cap, but has no total-minute balance unless one was explicitly snapshotted.
* `allowance` uses `InternetEntitlement.total_minutes_allowed` and `minutes_used` as
  its only finalized commercial balance. Membership benefits provision this same
  mode (for example, 6,000 benefit minutes), so they use the same reservation and
  settlement engine and do not decrement the legacy subscription field.
* `unlimited` is limited only by validity and any explicitly configured daily or
  session cap.
* Legacy `membership_credit` remains authoritative in
  `MembershipSubscription.remaining_internet_minutes`. It is reserved through the
  linked entitlement and decremented only on the subscription, never on both
  balances.

## Authorization and accounting policy

Session admission locks the entitlement row. Under that lock, Hub checks device and
concurrent-session limits and snapshots the minimum of unreserved total balance,
unreserved local-business-day balance, per-session limit, and complete minutes left
before validity expiry. A daily-limited session is also bounded by the next local
midnight. A finite authorization is reserved on the active session; genuinely
unlimited sessions have a null reservation.

Elapsed usage rounds every positive partial minute upward (`1s = 1m`, `60s = 1m`,
`61s = 2m`). Settlement locks both session and entitlement, caps consumed allowance
at the reservation, retains actual elapsed minutes, records any operational overrun,
and releases the reservation by ending the session. Finalized minutes are allocated
by the configured Django timezone to the business date on which each rounded minute
began, including sessions crossing midnight.

`authorized_until` is the commercial/accounting deadline. Manual networking cannot
physically enforce it. An active session after that deadline remains active but is
shown as overdue for staff to disconnect manually.

## Deferred network lifecycle work

MikroTik remains disabled. Planning reads Hub's central authoritative entitlement
allowance, but this change does not add RouterOS resources or external I/O inside
accounting transactions. Reliable post-commit refresh/disconnect retries remain for
the safe network provisioning/outbox work. Cancellation, expiry, freeze, reversal,
and forced-disconnection orchestration remain separate lifecycle work.
