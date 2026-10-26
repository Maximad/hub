# Hub Suite membership audit and readiness — 26 October 2026

## Existing behavior, verified from repository

- Member identity and claimable login outlive memberships.
- Subscription sale creates an order and payment (paid), or a zero-value order without a fake payment (free), and snapshots current plan benefits.
- Existing POS/QR eligible-product discounts are computed from active subscription snapshots; eligible discounts do not stack.
- Internet-minute benefits provision per-entitlement allowances, with partner revenue allocation and device/validity limits. No new MikroTik configuration is introduced by this change.
- Renewal creates a new sale. Pending future plans activate from the existing lifecycle; failed activation can be retried only against documented paid or complimentary provenance.
- The membership model also stores event discount, booking priority, workspace minutes, early access, free quantity, and member Internet price rules. A resolver or configured rule alone does not establish an automatic end-to-end redemption flow in every channel.

## Changes in this sprint

1. The staff sale form shows **automatic supported** versus **configured/requires review** rules.
2. False-positive preview promises for 0%, greater than 100%, and nonpositive fixed discounts are suppressed.
3. Internet-minute benefit configurations are **preflight checked** using the actual commercial provisioning validator before any sale order or payment: positive minutes, device limits, configured bandwidth profile, valid partner share/exemption, and total allocated Internet component not exceeding membership price.
4. Bad Internet-plan configurations show an explicit blocker in staff preview; the sale is refused before charging. Previously a sale could commit payment and later fail activation for known-invalid configuration.
5. A read-only `membership_readiness` command audits **configured plans on the connected DB** and returns an operator summary or JSON. It does not change plan availability, subscriptions, prices, network state, balances or allocation records.

```bash
python manage.py membership_readiness
python manage.py membership_readiness --json
python manage.py membership_readiness --all
python manage.py membership_readiness --strict
```

The default audit covers active staff-visible plans. `--all` also includes inactive/hidden plans for historical/configuration review. `--strict` exits unsuccessfully if an **offered** plan is blocked by known bad Internet provisioning configuration. Findings are from whatever Django database the command is connected to, not evidence of deployment from GitHub. Do not paste personal member information into reports.

## Practical scope to confirm before promoting membership sales

- Approve the actual list of plans, names, prices, validity terms, offered benefits and discount exclusions from the Hub commercial side. This sprint **does not** decide these.
- Run the read-only audit against staging and production after verified release. Confirm results with the actual configured Internet default partner; an invalid or absent allocation is a blocker, not an instruction to waive partner share.
- Perform one staff POS/member-QR eligibility purchase test and one validated Internet-minute membership activation. Confirm that financial posting and entitlement balances are correct.
- Keep benefits with no verified redemption path in staff-only *configuration review* until a separate, explicitly approved release. Do not market free products, workspace minutes, priority booking or event discounts as automatically redeemable merely because a rule field exists.
- Ensure current and previous merged migrations and deployment revision are verified by normal operations release checks.

## Deliberately not changed

- No plan seeding, new tiers, prices, membership discounts, benefits, promotional promises, data backfill, production records or migrations.
- No changes to MikroTik, provider share, member identity, historical sale snapshots or existing paid entitlements.
- No attempt to automate unresolved event/workspace redemption in this maintenance sprint.
