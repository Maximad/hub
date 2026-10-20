# Hub Sweida — source-linked count follow-ups (PR #326)

## Why

The owner explicitly prioritizes sale, purchasing, physical counting and Internet operations, and reports that many historical paper-ledger totals were manually combined and are unreliable. An imported zero is **not** stockout evidence. The first Smart Follow-up milestone must be useful without forecasting or full historical audit.

## Deliberately bounded scope

This adds a **manual opt-in** CLI pilot which creates up to 10 source-linked physical-count tasks for existing active materials, only on an open Business Day. It uses current `BusinessDayTask`, the current Active Duty holder, and confirmed count evidence from the stock-count screen. No scheduler integration, no new database tables, no automatic alert broadcast, no use of dubious historical sale totals.

Existing task keys are `verified-stock:needs-count:<InventoryItem.pk>` for the current Business Day. Repeating the command leaves the task unchanged and does not reopen waived/done tasks. Only a verified physical count resolves a pending task. Open days without confirmed roster duty produce an explicitly unassigned task; management can assign it through the existing task UI.

No `due_at` is set, deliberately preventing automated overdue notification spam.

## Steps after an approved merge and production deployment

1. Ensure the Business Day is open and a designated staff member is checked in, with inventory_stock_count permission.
2. If applicable, explicitly assign Operations Active Duty to that checked-in staff member on the existing Business Day screen. A roster alone is insufficient.
3. Select **no more than ten actual high-priority materials**; check IDs in the current stock list. Do not blindly use example IDs.
4. Execute the preview-only command:

       docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
         python manage.py sync_verified_stock_followups --ids 171,173

5. Verify source items, potential assignee, and whether each item still needs an evidenced count. The preview does not create any task or move money or stock.
6. Only after owner review, run with `--apply --actor maximad`, then inspect `/staff/operations/tasks/`. Run the same command again to confirm no duplicate new tasks.
7. Have a permitted operator conduct the **actual physical count** in `/staff/inventory/stock-count/`. Rerun the same CLI in preview to see `resolved_by_count`; explicit apply closes the pending follow-up.
8. Keep all newly generated tasks on the existing tasks screen, including uncovered work visible to managers.

## Safety

- The command does not change stock quantity, price, purchase, invoice, payment, recipe, Internet or historical orders.
- `--apply` requires an existing active manager user; default preview is rollback-only.
- No assumption that an unverified zero means empty stock; no automatic purchase suggestion.
- No jobs for all 283 materials, no notifications, no toggles changed, no RouterOS modifications.
- No auto-task completion from merely clicking a notification. A staff-confirmed physical count is required.
- Known limit: this is a daily projection, not yet a persistent multi-day review-case lifecycle. Cross-day carryover, duty handover re-assignment, forecast and supplier planning are later batches after real staff pilot.
