# Business Day — opening, handover and smart operations

This extends the Business Day layer added in PR #241. It does not replace `DailyClose`, order settlement, inventory workflows, shifts, or Internet lifecycle services.

## Opening workflow

When a Business Day is created, Hub seeds a required opening checklist:

1. review unresolved handover notes;
2. confirm the staff roster;
3. confirm opening cash/cashboxes;
4. confirm venue readiness and cleanliness;
5. confirm bar/kitchen and essential supplies;
6. review Internet status from Hub without modifying network configuration;
7. review unavailable/missing products.

The checklist records who confirmed each item and when. A required item may be waived only with a written reason. The roster item is controlled from the roster selector rather than manually toggled.

`opening_completed_at` is set only when every required opening item is done or explicitly waived. Reopening a required checklist item clears opening completion.

## Staff roster and lightweight check-in

Managers choose the active staff for the Business Day. This is an operational roster, not payroll attendance.

The roster is used for:

- daily-code reminders;
- operational handover targeting;
- showing who is expected on duty;
- recording a lightweight check-in when an assigned user successfully verifies the daily code.

Internet-provider accounts are not eligible staff roster accounts.

## Daily staff code security

The six-digit daily code remains encrypted on `BusinessDay` and hashed for verification.

A `pre_save` persistence guard on `NotificationEvent` removes any attempted plaintext daily code from the notification body before the database write. Staff notifications say only that the code is ready and link staff back to the authenticated operational page.

The code therefore must not be copied into:

- browser push payloads;
- notification history;
- audit details;
- logs;
- URLs.

## Handover notes

Any Hub staff user can create a handover note for the current Business Day. Notes may be normal or high priority and may target a specific staff member or the current team.

A note remains visible across operating days while it is `open` or `acknowledged`. Managers can mark it resolved. This prevents overnight issues from disappearing simply because the date changed.

## Smart indicators

The first anomaly rules are deterministic and advisory. They never post accounting entries or mutate orders automatically.

Current rules flag:

- absolute finalized cash difference above `BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP` (default 5,000);
- discounts above `BUSINESS_DAY_DISCOUNT_WARNING_SYP` (default 10,000);
- cancellations at or above `BUSINESS_DAY_CANCELLATION_WARNING_COUNT` (default 3);
- assigned staff who have not acknowledged/used the current daily code;
- unresolved high-priority handover notes;
- an incomplete required opening checklist.

These indicators are shown separately from hard close blockers. Existing `BusinessDayException` blockers continue to control whether the operating day can close.

## Owner digest

After a successful operational close through the staff Business Day workspace, Hub creates an in-app digest for active superusers (or active admins when no superuser exists). The digest includes:

- order count;
- gross sales;
- Internet revenue;
- discounts;
- cancellations;
- aggregate finalized cash difference;
- a short anomaly summary.

The digest is informational. Financial truth remains in the existing ledger and `DailyClose` records.

## Scheduled automation

Run:

```bash
python manage.py business_day_tick
```

The command is idempotent and intended for an hourly scheduler. It:

- optionally opens the Business Day when `BUSINESS_DAY_AUTO_OPEN_ENABLED=True` and the configured opening hour has arrived;
- reminds on-duty staff who have not acknowledged the daily code after the configured delay;
- reminds managers when the required opening checklist remains incomplete.

Useful optional settings (all have safe code defaults and require no configuration for manual operation):

- `BUSINESS_DAY_AUTO_OPEN_ENABLED` — default `False`;
- `BUSINESS_DAY_AUTO_OPEN_HOUR` — default `10`;
- `BUSINESS_DAY_CODE_REMINDER_MINUTES` — default `120`;
- `BUSINESS_DAY_OPENING_REMINDER_MINUTES` — default `90`;
- `BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP` — default `5000`;
- `BUSINESS_DAY_DISCOUNT_WARNING_SYP` — default `10000`;
- `BUSINESS_DAY_CANCELLATION_WARNING_COUNT` — default `3`.

`--dry-run` reports current pending counts and never sends reminders or opens a day.

## Boundaries

This feature does not:

- automatically pay or cancel orders;
- automatically close cash shifts;
- create financial `DailyClose` records;
- change RouterOS profiles/rates/HotSpot/firewall/NAT/DNS/DHCP/queues/certificates;
- treat daily-code check-in as HR attendance or payroll evidence.
