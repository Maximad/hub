# Staff permissions

Hub staff authorization has one source of truth: `accounts.permissions`.

## Effective permission model

Each staff account has a role that provides its default Hub capabilities. A
non-admin account may then have explicit per-user overrides stored in
`StaffCapabilityOverride`:

- `inherit`: use the role default;
- `allow`: grant the capability to this user even when the role normally lacks it;
- `deny`: remove the capability from this user even when the role normally has it.

Admin/superuser accounts always retain the full Hub capability set. Individual
overrides cannot deny an admin capability. This prevents a permission edit from
silently locking out the administration path.

Django's `is_staff` flag is separate. It controls access to `/admin/`; it is not
the Hub staff authorization mechanism.

## Role defaults

| Capability | Admin | Cashier | Waiter | Kitchen |
| --- | --- | --- | --- | --- |
| Staff operations | Yes | Yes | Yes | Yes |
| Orders | Yes | Yes | Yes | No |
| POS/order entry | Yes | Yes | Yes | No |
| Cashier/collection | Yes | Yes | No | No |
| Reports | Yes | No | No | No |
| Finance | Yes | Yes | No | No |
| Inventory | Yes | Yes | No | Yes |
| Reservations | Yes | No | Yes | No |
| Events | Yes | No | Yes | No |
| Settings | Yes | No | No | No |
| Imports | Yes | No | No | No |
| Users/permissions | Yes | No | No | No |
| Product modifiers | Yes | No | No | No |
| Internet billing | Yes | Yes | No | No |
| Members / Internet | Yes | Yes | No | No |
| Preparation board | Yes | Yes | No | Yes |
| Partial-payment approval | Yes | No | No | No |
| Order editing | Yes | Yes | Yes | No |
| Delivery management | Yes | Yes | Yes | No |

Cashier retains preparation-board access because non-kitchen preparation stations
such as bar/cashier/service are assigned to the cashier operator role. Waiters no
longer receive preparation-board access merely because they can enter orders;
they can be granted it individually if a real shift requires it.

## Enforcement

`user_has_capability(user, capability)` resolves the effective result. The same
resolver is used by:

- `@require_staff_capability(...)` server-side view authorization;
- staff navigation and interface controls through `staff_caps`;
- action-specific checks such as order editing and partial-payment approval;
- in-app notification visibility;
- Web Push queue targeting and the worker's final pre-send check.

Hiding a button is never the authorization boundary. Protected views/actions
must continue to use the server-side capability checks.

## Notification relationship

Notification role/station targeting decides who is a candidate recipient. The
required effective capability then decides whether that candidate may receive the
notification. User notification preferences and browser subscription state are
additional filters after authorization.

Examples:

- a waiter denied `orders` does not receive new-order alerts;
- a kitchen user receives new preparation alerts because the account both matches
  the kitchen station role and has `kitchen_board`;
- an admin sees preparation events in-app but does not receive a second
  preparation Web Push for every normal order;
- a ready preparation item is routed to service/waiter staff and requires
  `delivery_management`.

## Administration

The staff user create/edit screen exposes role defaults plus a tri-state override
for every Hub capability. The user detail screen shows the effective result and
whether it comes from the role, an individual allow/deny, or administrator full
access.

`StaffCapabilityOverride` is also visible in Django admin for technical recovery
and auditing.
