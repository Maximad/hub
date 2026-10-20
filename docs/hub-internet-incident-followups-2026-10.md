# Hub Sweida — guarded Internet provisioning follow-up

## Scope

Extend the existing `OperationalReviewCase` and `BusinessDayTask` follow-up mechanism to **confirmed** failures in the Django-owned package-less InternetSession network outbox. Do not build a second incident dashboard, restart a session from a task, change the MikroTik router, or expose any credentials or customer details.

## Evidence gate

An Internet session qualifies for a `network_review` only when:

- The session is currently ACTIVE and uses a managed network provider, not manual mode.
- Its current `network_status` is `provision_error`.
- A durable `InternetSessionNetworkOperation` of type PROVISION has status FAILED and `attempt_count >= 3`.

This distinguishes an actual unrecovered provisioning problem from routine pending and transient retry states. A session which finishes or successfully provisions causes the case to resolve on the next **explicit** review-sync. Only current recent failed operations (past 14 days) are eligible for opt-in `include_recent` discovery. Older sessions can be selected explicitly for review, but they are not automatically swept.

The existing BusinessDay review task has no due date, automatic reminder or self-healing action; operator assignment is visible only to an authorized checked-in Operations duty holder with the `internet_billing` capability, or to managers when uncovered. Pending handovers do not transfer responsibility. Task status cannot be manually marked Done.

## Security and privacy

Review titles and persisted labels are generic (e.g. `جلسة إنترنت #52`), without guest names, phone numbers, access codes, MAC addresses, IP addresses, network credentials, raw backend errors or payment status. Task metadata contains only the internal source ID and issue code. Staff links reuse the existing capability-gated `staff_internet_session` page.

This review layer NEVER calls RouterOS, network backend methods or worker processing; it never modifies InternetSession, InternetSessionNetworkOperation, payment, entitlement or accounting data.

## Optional rollout after tested release and authorized deployment

The command is not a staff testing requirement for the development phase. If an operator later chooses to review a real confirmed failed session (ID verified from the existing authorized Internet admin view), they may preview with:

    python manage.py review_operational_followups --no-recent --internet-session-id <verified_session_id>

This defaults to a full transactional rollback. `--apply --actor <manager_username>` requires explicit approval to persist a review case, not to provision access or charge money. The ordinary manager-only task screen can also opt-in to discovering recently failed durable outbox operations.

## Explicit deferrals

- No action or alert from historical handwritten sales or unrelated old sessions.
- No raw error text in a task or notification.
- No automatic recovery, retries, time charging changes or MikroTik configuration changes.
- No network incidents based on single failures; operator still sees the original network controls for supported recovery procedures.
- No blanket financial audit or assumption that all Internet billing records are accurate.
