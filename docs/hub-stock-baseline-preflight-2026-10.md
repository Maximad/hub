# Stock baseline preflight — first live assessment

This is **read-only**. It does not validate actual physical stock, cannot certify recipe suitability and must not create replenishment tasks or stock-deduction entries.

## Why this is required

On 20 October 2026, the owner supplied a read-only production query showing:

| Metric | Count |
|---|---:|
| Products | 146 |
| Available products | 86 |
| Inventory items | 283 |
| Active inventory items | 272 |
| Inventory records with current_quantity=0 | 264 |
| Missing reference costs | 207 |
| Existing recipe lines | 208 |
| Active recipe lines | 0 |
| Purchase documents | 1 |
| Purchase lines | 0 |
| Order lines | 48 |
| Order lines with stock_deducted=True | 0 |

These are **database counts**, not a physical stock certificate. In particular, 264 zero records cannot be labeled 264 stockouts, and it is not yet valid to calculate days of cover.

## Command

After review, merge, proper VPS deployment and deployed-SHA verification, run:

```bash
docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
  python manage.py audit_stock_baseline --limit 20 --days 28 --json
```

This reads order items, products, inventory and recipe data only. It returns product names and **no** customer, employee or supplier personal details. A separate locally held export can be used for reconciliation. The production web container cannot execute this command before the corresponding release is deployed.

- The report separates served operational order lines from marked monthly-aggregate legacy imports. It excludes cancelled orders/items. The identification of marked aggregates is conservative, not a proof that every old source is tagged. `served_line_units` does not certify completed payment, and does not by itself prove kitchen consumption.
- Prioritization ranks available products by recent served lines and separately labeled imported aggregates; it **does not** issue a purchase request.
- All recipe lines remain in their original active or inactive state.
- Legacy `InventoryItem.current_quantity=0` is not proof of a stockout. The future count-confidence engine will require a separately validated physical count and posting history.
- Drafts, trials, offered sugar and selected hookah flavor/brand require their own resolution before confident stock forecasts.
- The existing `BusinessDayTask` low-stock threshold logic must not be upgraded to forecasted expiry until the verified-count and recipe gates are implemented.

## Next work package

Map frequently sold products to stable catalog IDs and inventory SKU codes. Begin supervised counts with ingredients for the most relevant products, set supplier lead times, and verify recipe consumption for the selected sample. Keep old inventory records and financial history intact. Build the recurring roster and handover independently, preserving financial Shift/DailyClose boundaries.
