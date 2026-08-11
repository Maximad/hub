# Membership compatibility path

The existing `MembershipPlan` and `MembershipSubscription` tables are evolved in
place so foreign keys, member history, benefit rules, credit ledgers, and Internet
entitlement references remain valid. No legacy model or field is deleted.

Migration `members.0003` maps legacy billing periods to the generic vocabulary,
maps `paused` subscriptions to `frozen`, snapshots each existing plan price into
its subscriptions, and derives lifecycle timestamps from existing historical
timestamps. The legacy Internet-minute, credit, notes, benefit-rule, and ledger
fields remain available for compatibility; new generic membership code must not
treat them as plan capabilities.

Deploy this additive migration before changing callers to use generic plans,
subscriptions, or member attributes. Benefit evaluation, Internet entitlements,
program enrollment, and finance posting are intentionally unchanged.
