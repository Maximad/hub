# Staff Internet session recovery

Built from main `05eadb1` after fast Internet approval PR #336.

## Completed

- The existing session detail page exposes the last recorded access status, activation timestamp, billing evidence and the latest ten session network jobs. Provisioned means ready for a customer to connect, not proof that the customer device is online.
- Arabic guidance distinguishes failed, queued, processing and completed operations. Stored backend errors are shown through the existing safe-category formatter, now shared by the settings and session pages.
- Native GET refresh and POST retry work without JavaScript. Session retry retains both the existing session-view capability (`members/internet`) and existing network-retry capability (`settings`). Ordinary cashiers can read the status and request an authorized colleague's help.
- Retry calls the existing atomic outbox requeue service. It verifies the job belongs to the displayed session, locks the session and job, retains the original job and idempotency key, and adds the existing audit event. It does not call the router, create a session, sale, payment or new job, or reset the billing clock.
- Activation/refresh jobs for terminal sessions cannot be manually requeued, from either the session page or settings. Disconnect retries remain available. Duplicate/concurrent clicks queue and audit once. Audit failure rolls back the queue change and returns a safe retry message.
- Metered sessions whose existing durable activation gate is still closed show no billable preview or end/charge confirmation. Direct end-page access shows the reason and a return link. The existing finalization service continues to enforce the authoritative billing gate. Manual, legacy, package and free billing policies are preserved.
- Package-backed access shows the entitlement's recorded network status and links authorized staff to its existing settings operations. This change does not introduce a second package retry path.

## Verification

- Regression coverage includes read-only/safe display, permissions and revocation, POST/auth/CSRF, foreign and invalid job IDs, ended-session guards, disconnect recovery, repeated and concurrent retries, failed audit rollback, worker failure and success, clock preservation, and absence of duplicate commercial records.
- Existing full Django/PostgreSQL CI is required. The existing browser check also renders the real staff page at 320, 390 and 768 pixels in RTL without JavaScript and submits the native retry form against an isolated preview route. Fixtures contain no production data and must render without a database query.

## Risks and rollback

- Retry only queues work. Actual activation still depends on the existing Internet worker and backend. A stale page may report that another request already queued the job; refresh shows the current state.
- No schema migration, production data edits, deployment, or changes to MikroTik settings/profiles are included.
- Revert this code commit to restore the previous presentation and settings-only retry entry point. Existing session/job/audit history remains intact. The terminal-session guard added to the shared retry service is removed by that revert as well.

## Remaining

- Sales/payment recovery and customer account closure remain the next daily-operations review.
- This package does not revise automatic worker retry/lifecycle policy, prove device connectivity, refund package purchases, or change historical/manual billing records.
