# Hub Suite P0 — Cashier and payment production acceptance

**Status:** operator acceptance pending. This is an evidence checklist, not a claim
that production payments, device flows or release markers have passed.
**Scope:** existing Django cashier/Operations and finance diagnostics. No router
configuration, payment repairs, resets, synthetic production receipts or ledger
flag changes.

## 1. Establish exactly which release is running

On the VPS as `deploy`, inspect without printing secrets:

```bash
cd /opt/hub
git status --short
git rev-parse HEAD
cat backups/last_deployed_revision.txt
docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env ps
```

Record timestamp, deployed and source commit, whether they match, and whether
web, management-mcp, internet-worker and notification-worker are healthy.
The deployment script separately verifies backup/restore and release gates;
retain its successful output/evidence in the private operations archive.

## 2. Check existing records (no production mutations)

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env \
  exec -T web python manage.py audit_customer_payments --recent 10 --json
```

A PASS covers **only** the sampled records. Classify each WARN by payment/order
and business date before taking further action. Do not backfill a journal or
record replacement cash in order to hide an exception.

### Case: order #00024 appears under standalone, but shows a table

This is **not automatically a bug**: cashier grouping uses `Order.visit_id`,
not `Order.table_id`. An order can have a table and no shared visit account.

After the order-inspection enhancement in the P0 PR is **merged and deployed**:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mikrotik.yml --env-file .env \
  exec -T web python manage.py audit_customer_payments --order-id 24 --json
```

Read `selected_order.cashier_group`, `selected_order.table_assigned`,
`selected_order.visit_code`, and its actual amount paid/remaining. If
`cashier_group=standalone` and `table_assigned=true`, the standalone bucket
is consistent with the database. Whether the order *should have belonged* to
an existing visit is a separate staff workflow question. Never attach it to a
visit or recreate payments without an authorized, audited correction.

Before this enhancement is deployed, do **not** run `--order-id` against the
old production command. Use `--recent` or the existing order screen instead.

## 3. Cashier acceptance matrix

For each result, capture **PASS / FAIL / NOT TESTED**, role, device/browser,
approximate local timestamp and a short non-sensitive note.

| Case | Expected behavior | Status |
| --- | --- | --- |
| Cashier landing | Separate open visits and standalone orders; display genuine totals and locations | NOT TESTED |
| Table without visit | Appears as independently payable order; never silently joined to a table's visit | NOT TESTED |
| Visit order | Multiple visit-linked orders roll into the one visit balance, with no duplicate standalone row | NOT TESTED |
| Payment history | Authorized cashier can see active collections and correct remaining amounts | NOT TESTED |
| Operations refresh | After a legitimate inline mutation, card counts/balances refresh without requiring drawer closure; fall back to full page if network fails | NOT TESTED |
| Duplicate submit | The *same* submitted request cannot create duplicate collection; a separately approved second payment is distinct | NOT TESTED |
| Settle/close | Finalize active metered Internet before calculating final due; no debt silently written off | NOT TESTED |
| Finance permissions | Cashier cannot reverse a receipt without the existing authorized finance approval and evidence | NOT TESTED |
| Read-only diagnostic | Known payment/visit/order shows consistent postings and movements or a reproducible warning | NOT TESTED |

Use real legitimate operational events or staging for financial write cases.
A dashboard screenshot alone is not evidence of POST idempotency, journal
accuracy or device-level Internet acceptance.

## 4. Internet phone acceptance remains separate

Follow `docs/internet-consolidated-release-2026-10.md` cases A–I with real
Hub phones. Confirm basic Wi-Fi and customer purchase independently; test
staff grants and provider isolation. **Do not** change MikroTik settings,
configure speed profiles or enable staff minute/device quota enforcement.

## 5. Decision for the next bounded PR

Prioritize reproducible failures using this order:

1. Data-inconsistent collections or missing journals verified by record ID.
2. Orders wrongly attached to visits, or real bills that cannot be settled.
3. High-frequency cashier balance/refresh, permission, or receipt failures.
4. Device-specific Internet UX observed on actual phones.
5. Cosmetic polish after accounting and connectivity acceptance.

Write a targeted Django regression test before changing behavior. Keep
historical records, protected ledger flags and RouterOS configuration intact.
