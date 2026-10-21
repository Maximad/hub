# Fast Internet staff approval — 21 October 2026

Builds on main `6ce96d6`, including purchase receipt recovery PR #335. This is the next bounded Internet package from the daily operations handover.

## Completed behavior

- New customer fast access requires staff approval by default. The checkbox **اشتراط موافقة الموظف لكل جهاز خلال زيارته** on the existing Internet settings page turns the requirement on/off. Only the existing `settings` capability can change it. A fresh database read enforces this switch across Django workers, independently of the per-process settings cache.
- Approval belongs to the existing browser credential of the selected Hub visit. It covers this device for this visit; another browser or a new visit requires its own approval. Closure or credential revocation makes approval unusable. Approval is permission to start, not a free service or proof of payment.
- Wi-Fi and My Session show a request action, pending/rejected state and explicit refresh action. The request number matches the device number on the staff screen. Standalone visitors supply the same required name/phone as the existing fast Internet identity flow.
- Staff with the existing `members/internet` management capability review pending requests on the existing Internet screen. Requests are paginated; rejected requests have a bounded collapsed reconsideration list. Review records the actor/time and an ActivityLog event. Repeated and stale reviews cannot silently overturn a newer decision; reconsidering a rejection requires the explicit rejected-state form.
- Requesting or approving creates no sale, payment, usage session or network job. After approval the customer refreshes and starts the desired service on the same device. Existing pricing, entitlement ownership, balance/device limits and network-activation billing gates remain authoritative.
- New metered, package, existing-entitlement, legacy visit and legacy cart starts enforce approval server-side before commercial/network side effects. The cart compatibility path binds newly created sessions to the initiating credential; old sessions are not rebound.
- Existing active fast sessions retain their current access and retry/end behavior when the switch is turned on. Basic/slow Wi-Fi remains available while waiting; internal owner/team grants retain their separate staff identity and authorization policy.
- Duplicate request/review and rollback tests include concurrent requests/reviews on PostgreSQL. Existing self-service tests explicitly select the off policy to retain coverage of direct-start behavior; new tests exercise the default on policy.

## Schema and release

Core migration `0060_fast_internet_staff_approval` adds the policy Boolean (default true) and approval state/request/reviewer metadata to the existing `HubVisitBrowserCredential`. The two non-null columns have persistent database defaults, so previous code can still insert records during a code rollback. No new task engine, access model or RouterOS setting is introduced. Existing credentials start as not requested; existing active sessions are grandfathered. No history is backfilled with fabricated approval evidence.

The full Django/PostgreSQL suite and existing browser CI verify the package. The new browser helper renders real customer/staff templates at 320, 390 and 768px with JavaScript disabled and unsaved synthetic data. CI results are recorded in the PR.

## Risks, rollback and remaining work

- Staff must identify the exact request/device, especially when several devices share a bill. Approval is scoped to the browser credential, not a hardware-attested device identity. Clearing the cookie requires a new approval.
- Approval is per browser per open visit, not a fresh review on every reconnect or package choice. Staff must close completed visits through the existing workflow.
- Switching the requirement off permits immediate new customer starts. It does not automatically start pending requests. Switching it on does not disconnect sessions already active.
- The approval page refresh is explicit and works without polling/JavaScript, which keeps the flow usable on weak connections.
- Operational rollback: switch the requirement off from Internet settings. Code rollback can leave additive fields in place; preserve approval audit history. Do not reverse the schema migration merely to discard records.
- RouterOS, speed profiles, production data and deployment are untouched. Deployment/migration requires separate explicit approval.
- Broader network activation/failure UI review and sales/payment recovery remain separate work. Existing durable network jobs and OperationalReviewCase evidence continue to own network incident follow-up.
