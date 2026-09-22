# Hub Business Day operations

The Business Day layer coordinates venue operations. It does **not** replace the financial ledger or `DailyClose`.

## Operating-day boundary

`BUSINESS_DAY_CUTOFF_HOUR` defines when Hub changes business date in the `Asia/Damascus` timezone. The default is `04:00`, so activity after midnight and before 04:00 still belongs to the previous business date.

Only one Business Day may be operationally open at a time. Its lifecycle is:

`OPEN -> CLOSING -> CLOSED`, with an explicit admin-only `REOPENED` path.

Reopening requires a reason and issues a new daily staff code.

## Daily staff code

Opening a Business Day issues a random six-digit staff code.

The code is deliberately separate from:

- guest Wi-Fi venue codes;
- staff account passwords;
- manager approval credentials;
- financial reopen authorization.

The server stores a password hash for verification and an encrypted short-lived copy so authenticated staff can reveal it during the operating day. Closing the Business Day deletes the encrypted copy. Rotating the code invalidates the previous value immediately.

Active Hub staff receive an in-app notification that the code is ready. Internet-provider accounts are excluded. The plaintext code is not retained in notification history and the daily-code event uses the existing non-push daily notification route, so it is not delivered to lock screens through browser push.

For each user Hub records whether the current code version was notified, viewed and used. Failed and successful verification attempts are audit logged.

The shared daily code is an operational convenience only. It must not authorize refunds, financial-period reopening, price changes, destructive record actions or other manager-level financial actions.

## Closing reconciliation

The staff `/staff/close-day/` workspace is the operational close surface. It scans the Business Day and separates findings into blockers and warnings.

### Blockers

Current blockers include:

- non-terminal orders;
- unpaid balances on non-cancelled orders;
- active metered Internet sessions;
- open staff shifts;
- a cashbox used during the day without a finalized account `DailyClose`;
- reopened/non-final financial closes for the business date.

A blocker cannot be carried forward. It must be resolved before final operational close.

### Warnings

Current warnings include active non-metered Internet sessions and open Hub visits. Warnings may be resolved or explicitly carried forward with a reason. Carry-forward is recorded on the Business Day exception.

The exception list is synchronized from current application state. If a previously open problem disappears, Hub marks that exception resolved automatically. If the underlying problem returns, it becomes open again unless it was explicitly carried forward.

## Safe cleanup actions

### End open Internet sessions

The close workspace can end active Internet sessions using the existing session lifecycle only:

- package-less metered sessions are finalized through the normal metered billing service;
- entitlement/basic sessions are ended through the normal usage-session service;
- existing session deauthentication/disconnect operations are queued where applicable.

This action does **not** modify RouterOS profiles, rates, HotSpot configuration, firewall, NAT, DNS, DHCP, queues, certificates or other MikroTik configuration.

A failed session does not stop the whole cleanup pass. Failures are returned as safe application exception categories and remain visible through reconciliation.

### Close safe visits

Hub may automatically close an open visit only when it has:

- no active Internet session;
- no non-terminal order;
- no outstanding order balance.

Anything financially meaningful or still active is left open for staff resolution.

## Financial close remains authoritative

`core.services.posting.closing` remains the only authoritative account-period financial close service. Business Day does not calculate or post replacement financial balances.

When a cashbox was used by a shift during the operating day, Business Day requires the corresponding finalized `DailyClose` for that account and business date before operational finalization.

This preserves the existing account-level ledger snapshots, revision history, approver rules and audit events.

## Finalization

Final operational close:

1. refreshes reconciliation;
2. refuses to close while any blocker remains;
3. stores an operational snapshot with counts and carry-forward state;
4. expires the current daily code;
5. records closer/time in the audit trail;
6. creates the existing daily-close staff notification.

Operational close is therefore a coordination layer around existing domain workflows, not a bulk destructive action.

## Initial rollout scope

This first release provides:

- Business Day lifecycle and 04:00 default cutoff;
- daily staff-code generation, rotation, reveal and receipt tracking;
- in-app staff notification of code availability;
- closing reconciliation and persistent exception inbox;
- safe Internet-session cleanup;
- safe visit cleanup;
- financial-close blockers;
- final operational snapshot and reopen workflow.

It does not automatically close staff shifts, automatically mark orders paid/cancelled, create financial `DailyClose` records, or change MikroTik configuration.

## Follow-up phases

Recommended follow-ups after the closing workflow is operationally proven:

- opening checklist and shift handover;
- configurable code recipient groups/scheduled staff;
- scheduled “close due” and “close blocked” reminders;
- owner end-of-day digest;
- deterministic anomaly rules for voids, discounts, cash differences and unusual complimentary access;
- targeted nightly stock counts and recipe-vs-stock exceptions;
- Event Mode linked to Business Days;
- unified audit timeline and manager operational-health strip.
