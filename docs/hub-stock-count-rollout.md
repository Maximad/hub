# Launch P0 — physical stock count rollout

**Status:** implementation proposed in the corresponding PR; do not claim it is
deployed, tested on staff devices, or financially valued at opening stock.

## Who may use it

- Full access/admin accounts, including superusers.
- The seeded **تشغيل وكاشير** access profile.
- Other explicitly authorized users granted `inventory_stock_count` in
  staff user permissions. Explicit deny overrides win except superuser.
- **Not** anyone merely granted `inventory`: kitchen, bartender and waiter
  have no stock-count capability by default.

Use the existing Access profiles and staff-user permission editor to grant or
deny it. Do not give blanket `finance`/admin rights to enter counts.

## What it does

An authorized operator visits **المخزون → جرد المخزون**, finds an active
InventoryItem, enters the *actual counted quantity* in the item's existing
unit, and reviews the delta. Zero is valid; blank is not counted. Changes
require a reason. The confirmation is bound to the employee and a short-lived
signed preview; if an item's quantity or unit changed, the confirmation is
rejected and must be previewed again.

A changed count creates exactly one `StockMovement.CORRECTION` and immutable
`AuditEvent` with old/new quantities and reason. It uses row locking, a
unique idempotency key, atomic write and existing StockMovement validation
to prevent retry duplication, unexpected negative stock and stale overwrites.
Unchanged quantities create no adjustment.

**This changes operational physical quantities only.** It does not create a
purchase, supplier payment, cash movement, opening stock valuation journal
or automatic owner contribution. Estimated movement cost is informational;
the opening-balance accounting/valuation policy still needs separate owner
approval before any journal/backfill implementation.

## Release safety

Run Django/PostgreSQL CI including the count tests and migrations. Deploy
normally after approved merge, backup and restore verification. Preserve
pre-launch records; do not reset the database. On a designated staff device,
verify search, real units, zero/blank difference, reason, preview, retries,
stale-count rejection and denied users.

**No RouterOS, MikroTik, financial rollout flag or provider-portal changes.**

## Follow-up

Adapt purchase and supplier payment forms using the system-wide design
`Hub_Suite_Adaptive_Forms_Stock_Count_UX_Design_2026-10-14.md`.
Implement field dependencies in small modules with server-side validation
and JS-disabled fallbacks; do not silently infer a supplier payment from a
purchase draft.
