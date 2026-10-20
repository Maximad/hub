# Hub Suite — Smart Operations implementation, October 2026

**Status:** development plan; no production database or MikroTik changes authorised.

## Sources and constraints

- [LIVE Inventory & Recipes](https://docs.google.com/spreadsheets/d/1Tdf-DcDamo32DX17juD6YLEj4B1dL3knqmYe6eudwlk/edit), especially the `10_الوصفات_الموحّدة` tab.
- [LIVE Purchasing & Unit Costs](https://docs.google.com/spreadsheets/d/18ffI0jWaYAOInyxPWkE0UURR1LDI9uyI3EzZRtsB8nQ/edit).
- Existing `BusinessDayTask`, `BusinessDayDutyCoverage`, `Shift`, `Purchase`, `PurchaseItem`, `ProductRecipeItem`, `InventoryItem`.
- Never reset prelaunch operational data or rewrite recorded order/payment history. Specifically preserve order #22 and payment #1. Never alter MikroTik/Cudy/ISP settings. Do not infer a trustworthy counted stock quantity from a legacy zero.
- New Syrian lira: verified historical old-lira amounts are divided by 100; conversion is **not** guessed from amount magnitude. Keep both original amounts and provenance.
- Use separate staff accounts for Tamer and Salwa; manager supervises and handles exceptions, not daily purchasing/counting.

## Work packages and acceptance

### A0: Preflight, reconciliation, backups (this PR starts here)

1. Verify the live VPS revision separately from GitHub main, installed migrations, feature flags, `docker compose ps`, latest database/media backup and successful restoration evidence. Do not claim VPS is verified before actual readback.
2. Export reviewed unified recipe tab as a UTF-8 CSV file. Run a **read-only** preliminary matching report:
   `python manage.py review_hub_recipe_sheet --file /path/to/recipes.csv --json`
3. Inspect all rows requiring inventory SKU/product-key mappings, unit transformations, batch yield, and consumption-vs-offering rules. The output is **not an import file**. A row marked review_candidate is still not authorised to write.
4. Add separate read-only purchase price provenance checks in a later iteration. No direct SQL stock updates, fabricated purchases or automatic sale-price changes.
5. Build an approved ID-keyed import manifest, compare its effects to preflight counts, test on an isolated PostgreSQL copy, and require a reviewed explicit apply stage with idempotency, audit and rollback plan. Never reuse the old `import_inventory_items` against a review workbook: it writes physical quantities.

### A1: Verified prices and recipe versions

- Preserve purchase invoice prices, supplier, date, size, quantity, original currency and validated converted value. Current `InventoryItem.estimated_unit_cost_syp` is an estimate, not reliable last-invoice history.
- Purchase form shows latest verified quote and permits fast override; draft creation must not receive inventory or pay a supplier. A verified receipt may update the chosen price reference, never sale price.
- Product recipe quantities in the review sheet include confirmed and provisional values. Do not activate draft recipes or stock deduction automatically.
- Specific safeguards: maté offered sugar is not proven consumed; hookah selected tobacco brand/flavor requires option-specific stock mapping; chai khamir is a batch (10 Earl Grey bags, 15g tea, 6g cardamom for ~25 glasses); 600g orange for a 350ml cup is a one-run trial; sahleb is 1 sachet + 1 dry-milk sachet + water, with unresolved contradictory price sources (10 vs 95 new lira).
- Version future recipe changes prospectively; old order cost snapshots and financial postings never rewritten.

### B0: Recurring operating roster, without fabricated financial Shifts

- Template for Tamer (09:00–14:00 and 21:30–00:00) and Salwa (14:00–21:30) on normal operating days, with Friday evening-only opening and Tamer off, and Sunday flexible/Salwa off. Incomplete Friday and Sunday coverage stays explicitly unassigned until a person is confirmed.
- Weekly templates, date-scoped exceptions/closures, planned start/end, optional event staffing. Publish planned roster before opening; planned assignment is **not** attendance, Active Duty acceptance or a financial Shift.
- Check-in starts actual operational responsibility; cashbox opens through the existing financial flow only. Accepted handover routes outstanding responsibilities, with audited sender/receiver and measured cash vs expected cash.
- The financial `DailyClose` and Business Day close retain their own safeguards; handover is not a payment or a settlement.

### B1: Contextual daily tasks and modes

- Extend existing daily tasks, never invent a second task engine. One long-lived review case per source+reason+version; the current Business Day surfaces its one actionable projection.
- A relevant sale may prompt a one-tap recipe check after service, not an alert on every order; purchasing resolves quote verification; inventory count resolves unknown quantity; new item trials wait for required inventory to be physically received.
- Route work to actual operator or current eligible Active Duty; no assignment to off-duty users. Uncovered work is displayed to the manager, not sprayed across all users.
- Prep station Direct, Team and Event profiles already exist in code. Direct is intended for the current one-person-at-a-time setup after real-device acceptance; Team/Event remain available. Event scheduling does not auto-switch station modes in the current implementation.
- Notifications are limited/deduplicated and recover after accepted handover; a notification read is not proof of task completion.

### B2: Forecast-driven stock attention

- Compute ingredient demand from verified historical sales x effective recipe per unit, by actual opening day/hour, with a short (~7 active days) and medium (~28 active days) trend, and known scheduled event uplift where documented.
- Count only genuine consumption once: exclude cancelled orders and do not add the same demand to already posted sale stock movements. Historical monthly aggregate imports must not masquerade as daily observations.
- Inventory availability must be grounded by a trusted actual count and subsequent recorded movements. Unknown legacy zero => **needs_count**, not out_of_stock.
- Forecast `days_cover = available / credible_expected_daily_use`, if denominator > 0; purchase suggestion covers verified supplier lead days + safety days minus available and committed incoming receipts.
- Use `enough / reorder_soon / risk / insufficient_data` and confidence rating. Unknown recipe, unmeasured offering, range, option-specific ingredient, unsuitable unit or missing count => insufficient_data and staff review, not confident automatic purchasing.
- Existing fixed low-stock thresholds remain an independent safety floor. Only one source-linked task per material/reason, with periodic re-evaluation after count/receipt/recipe approval.

## Delivery gates

- GitHub branch + PR per bounded work package; full Django/PostgreSQL and migration checks in CI.
- Stage on disposable test data, including multi-shift handover, staff permissions, recipe import dry-run, Arabic mobile entry, partial receipts, currency conversion, cost discrepancy and predicted stock cases.
- Production deploy only with owner approval, verified backup + restore, deployed-SHA proof and tested rollback.
- Do not enable stock deduction or financial posting flags as a side effect of importing recipes.
- Do not update financial master Google Sheet or create purchase/expense/order records from historical cost notes.
