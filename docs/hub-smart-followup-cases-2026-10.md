# Hub Smart Follow-up: operations-first review cases (PR #326)

## Purpose
Historic purchases and aggregate sales imported from paper may be incorrect and are being corrected gradually. The operational priority is selling, buying, stock counting and Internet. A product may be usable while its recipe or costs remain incomplete. Follow-up works from observed operator actions and reliable evidence, not historical aggregate imports.

## Architecture
- OperationalReviewCase persists one source-linked issue per (source type, source ID, issue code), across Business Days, separate from financial certification.
- The existing BusinessDayTask is the daily projection with stable fingerprint review:case-id, not a duplicate task engine.
- Dynamic checked-in operations duty holder with capability is the only staff audience; missing coverage falls back to the manager review queue. Pending handover never transfers an issue.
- Source correction closes an open case: a physical count resolves needs_count; an active valid recipe resolves recipe_review; a positive current product price resolves price_review.
- Review tasks cannot be marked Done/Waived or manually assigned. Past Business Days remain intact.
- Review tasks have no deadlines; they cause no overdue push reminders.

## Evidence and rollout
- Candidates arise from genuine recent OrderItem operations in the past 14 days, explicitly excluding legacy aggregate lines containing import_key=, cancellations, and from actual recent purchase-receipt movements.
- Initial cases can also be selected by exact product IDs or inventory IDs (not fuzzy name matches). At most 20 new source candidates per sync (maximum 30). Previously created cases are always rechecked.
- The command defaults to an entirely rolled-back preview; the staff tasks UI offers a separate manager-only opt-in sync button.
- Business day tick, web push, order-save signals and migration do not enable any follow-up sync.
- The sync does not write orders, payments, stock, recipes, Internet sessions or any financial journal.

## CLI: Preview first, then deliberate application
For a known approved product/material pair, run on the deployed release after verified backup:

    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py review_operational_followups --no-recent --product-id 109 --inventory-id 171

The preview prints the proposed cases and rolls back. After checking the current IDs, open Business Day, and expected proposals:

    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py review_operational_followups --no-recent --product-id 109 --inventory-id 171 --apply --actor maximad

Omitting --no-recent includes up to the candidate limit from recent actual work. Neither step modifies stock, prices, recipe activation, network profiles or cash. Repeating apply is idempotent.

## Operator workflow
1. Continue normal sale, purchase, count and Internet actions in existing Django pages.
2. Manager opens /staff/operations/tasks/ and clicks مراجعات من العمليات الحديثة after real operations.
3. Holder of current accepted operations duty, when appropriately authorized, opens linked stock count or recipe. Uncovered cases are manager-visible.
4. After source work is properly completed, rerun review sync to resolve it. No manual Done shortcut is offered.
5. Record staff-device results before enabling any future automatic event-based sync.

## Deferred
No historic data backfill, no automatic purchasing, no auto stock deduction, no forecast from paper records, no RouterOS changes, and no final financial certification.