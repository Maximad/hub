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

Migration `members.0004` evolves the existing `MembershipBenefitRule` table in
place; it does not introduce a second benefit model or change any existing rule
ID or foreign key. The former target fields (`product`, `category`, menu section,
tag, and product-type fields) remain supported as stable scopes. Existing percent,
fixed, quantity, and minute values are copied into the generic type/value fields,
while every legacy column is retained for the established member-recognition
workflow. Existing subscriptions receive a JSON snapshot of their plan's active
rules. New subscriptions take the same snapshot when created, so later plan edits
do not rewrite historical subscription benefits.

The generic resolver API lives in `members.benefits` and is re-exported from
`members.services`. It only discovers active subscriptions and computes benefit
values. It never creates an Internet entitlement, order, payment, discount,
ledger entry, allocation, or network provisioning request. Vendor products are
excluded unless a rule explicitly opts in through metadata, and new product/event
discount rules require an explicit stable scope.

`Program` and `ProgramEnrollment` are operational participation records, separate
from subscriptions. An enrollment can optionally link to a member, subscription,
order, and payment; a child or other non-member participant instead supplies a
name and minimal JSON metadata. Program deactivation and edits do not delete or
rewrite enrollment history.

Production deployment is intentionally additive:

1. Back up the PostgreSQL database using the normal production backup procedure.
2. Deploy the application revision without changing Internet/network environment
   values.
3. Run `python manage.py migrate members` (or the standard all-app
   `python manage.py migrate`).
4. Run `python manage.py check`.
5. Restart the existing web and worker processes.

No entitlement backfill, discount regeneration, finance replay, partner-share
recalculation, or MikroTik action is required or permitted for this migration.
