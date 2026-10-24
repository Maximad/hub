# Fast Internet approval expiry — Sprint 4 (25 October 2026)

Status: draft development pending CI, not deployed.

## Decision
- An unanswered fast Internet consent request expires after **30 minutes** by default. A manager may configure **5–240 minutes** in Staff → Internet Settings. Existing enabled/disabled consent toggle remains independent.
- Even a newer request expires at the venue's existing business-day cutoff (configured `BUSINESS_DAY_CUTOFF_HOUR`, default 04:00 in Damascus). This is a request policy, **not** closing a visit or Internet session.
- New explicit `expired` state, not `rejected`. This distinction is required for accurate history and reporting.
- A request is removed from both staff Operations and Internet pending queues as soon as its deadline passes, **even before the scheduled worker persists the state**. Device status is presented as expired without altering GET requests.
- `business_day_tick` persists expired states and audit entries even if no Business Day is open. `--dry-run` counts without changing records. The worker is bounded, idempotent, and locks a visit before its credential, consistent with the existing approval/visit services.
- A late staff approval is rejected and persists the expiry. A new customer request reuses their browser credential and stores new request evidence. Existing approved access, rejected/requested reconsideration rules, paid sessions, packages and financial obligations are unchanged.
- Staff with Internet permission may view the last 40 immutable event logs: sent, approved, rejected, expired. Re-requests remain separately represented as ledger events even though the same device credential is reused.
- Existing auth, CSRF and idempotency remain authoritative; no router interaction, Internet allocation, payment, or sale is performed during expiry.

## Release checks
1. Apply additive migration `core.0061`, and validate existing/old Django writers with the new default.
2. Full PostgreSQL CI and device/browser check. Test at the 04:00 boundary, timeout, repeat tick, concurrent review, staff/guest permissions, rejection state, change of timeout, and event history.
3. Verify production task scheduler invokes `business_day_tick`. Queue hiding works without it; durable expiration events require ticks.
4. Staging smoke test: 30-minute idle request leaves live queues, customer sees expiry and can submit again; staff sees audit. Confirm old toggles remain correct.
5. Check deployed version and backup/restore, and deploy only with operational release authorization.

No MikroTik configuration changes. Never treat unanswered requests as unpaid sales.
