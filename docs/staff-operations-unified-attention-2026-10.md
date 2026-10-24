# Staff Operations — Sprint 3 unified attention (25 October 2026)

Status: draft PR, CI and nonproduction staff acceptance pending. This sprint is scoped to read-only aggregation and existing audited actions.

## Implemented
- Compact priority inbox above the order board, visible only when relevant urgent tasks or pending fast Internet approvals exist.
- Existing Business Day tasks: pending, high priority / required / due within 2 hours / explicitly assigned to the logged-in user. Show overdue first, then high priority. Display up to five with full count and a link to the existing Tasks page.
- A regular staff member sees their assigned work and unassigned work for their role. Staff without explicit operations review duty cannot see source-review tasks; those remain handled through the existing qualified task-review page. Managers can see role-wide tasks.
- Reuse the existing role/capability and task-status service for the inline **تم** action. Source review cases never have an inline completion action.
- Pending fast Internet approvals are shown only if the Django approval toggle is on **and** the staff account has `members/internet` permission. Require open visits and nonrevoked browser credentials. Show up to three with full count and a link to the existing Internet screen.
- **موافقة** / **رفض** reuse `review_approval`, with the existing actor audit and no Internet sale, usage session or network operation. Explicit `return_to=operations` redirects back to the dashboard; old routes retain their previous behavior.
- Full read is nonmutating: it never creates/finishes tasks, expires requests, affects any financial record, invokes a router or changes MikroTik configurations.
- Native responsive CSS, accessible 40px buttons, no new JavaScript or UI framework.

## Deliberately separate work
- Expiration and archival of aging Internet approvals (Sprint 4), including changes to the request lifecycle.
- Neighbor benefits and order creator/beneficiary attribution.
- No financial settlement or inventory changes; no schema migration.

## Verification
```
python manage.py check
python manage.py test core.tests.test_staff_attention_unified core.tests.test_staff_operations_attention operations.tests.test_operational_tasks core.tests.test_fast_internet_approval
```
Full GitHub PostgreSQL workflow. Check logged-in admin, cashier, kitchen, and waiter. Confirm permissions, priority sorting, date rollover, CSRF, mobile at 320 and 390 px, and both approve/reject and task-done return paths. Production deployment only after normal authorization and on-site staff acceptance.
