# Active Duty foundation — Phase 2a

## Separation of concepts
- **Roster** (`BusinessDayStaffAssignment`): who is scheduled for the Business Day.
- **Check-in** (`checked_in_at`): lightweight confirmation after valid staff daily-code use; **not** payroll/HR attendance.
- **Duty coverage** (`BusinessDayDutyCoverage`): who currently owns each operational responsibility, using a pointer to an existing checked-in roster assignment.
- **BusinessDayTask and HandoverNote**: retain their independent task and handover history. Duty coverage does not replace them.
- **Financial Shift**: remains the cashbox/financial ledger mechanism, never inferred from Active Duty.

## Workflow
Managers may explicitly assign a checked-in, capability-authorized, active
rostered employee to service, cashier, bar, kitchen or operations. No initial
coverage is inferred from the list of registered staff. Zero coverage is
deliberately visible as **uncovered**.

The current holder can propose another eligible roster member. Responsibility
remains with the current holder until **the intended replacement personally
accepts**. A manager may explicitly reassign responsibility, invalidating a
pending proposal. All non-idempotent transitions are recorded in ActivityLog.
Rows are locked within one Business Day transaction.

Existing `set_staff_roster` refuses to remove a person who currently owns a
duty or is the pending handover recipient. Existing order/payment/preparation,
guest-Wi-Fi, Business Day closing, staff-code and notification flows are
unchanged.

## Routing is deliberately NOT switched on in this PR
`current_duty_users(day, responsibility)` is a strict, read-only resolver.
It returns an empty result when no eligible, checked-in holder exists, and
never falls back to every active account. It also rejects closed days,
inactive staff and access revocations.

The next PR must bind notification categories to appropriate duty coverage
through effective capabilities and station context, with a safe and visible
unassigned queue and manager escalation. Do **not** silently route every
order to all staff, nor suppress orders during an unconfigured rollout.
Until that separate rollout is reviewed, existing Web Push routing continues
unchanged.

Open points for the next batch: on-duty backup / temporary replacement,
stale handover expiry, optional duty release with accountable unassigned state,
in-app handover proposal prompts, queued-work ownership and specific station
separation for kitchen vs bar. Existing service and cashier permission models
must remain authoritative.

## Validation
- New `operations.0004` table only, no modifications to historical shifts.
- Role/duty and day-boundary isolation; roster membership + check-in + effective
  capability; unauthorized and duplicate acceptance; pending handover doesn't
  move live responsibility; manager takeover and audit; no roster removal while
  someone holds a current/pending duty.
- Business Day UI is an additive section, respecting the existing navigation,
  permission checks and POST+CSRF.
- Full Django/PostgreSQL CI before merge, followed by an explicit staff-device
  acceptance test and separate deployment decision.

No MikroTik, payments, Internet provisioning, guest PIN delivery, or live
notification routing changes.
