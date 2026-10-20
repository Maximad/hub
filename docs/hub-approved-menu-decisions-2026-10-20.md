# Hub Sweida: approved catalog and recipe decisions (2026-10-20)

## Scope

The operator explicitly approved the initial product-to-SKU review and requested:
- Create **سحلب** and **شاي ظرف** as sale products.
- Make existing **نسكافيه 3x1** available (existing DB product 322, price 100).
- Create separate stuffed-olive stock material rather than treating plain green olives as stuffed.
- **شطة** in شوت دودو means **تاباسكو**; the dose is two drops, not an unverified 2 ml equivalent.
- Keep lemon stock clear: raw lemons in kg are distinct from freshly squeezed lemon juice in liters. Record actual raw kilograms and measured juice yield when preparing; do not assume kg-to-ml conversion.
- Sell **شاي خمير** by the glass, not as a compulsory POS production-batch workflow.
- **معسل الأركيلة** is tobacco, never food honey; flavor and brand inventory must be approved before linking hookah recipes.
- Do not delete or alter carafe or any ambiguous existing product based on the unclarified phrase "مسح الكراسة".

## Command

From the correct deployed Hub release and within the production Django service:

```bash
docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
  python manage.py apply_hub_menu_decisions --json
```

The command **defaults to preview only**, runs a full transactional simulation, rolls back everything, and prints a structured diff.

After inspecting the complete diff, verifying backup freshness, staging tests and confirming the intended staff actor:

```bash
docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
  python manage.py apply_hub_menu_decisions --apply --actor <existing_active_staff_username>
```

This explicit action changes only approved product, SKU, recipe-draft and availability fields; it does not alter existing sale prices, cash, historical orders, physical counts, purchasing records, router settings, or recipe stock-deduction activation. The command refuses any changed expected product IDs or SKU codes and aborts atomically on conflicts.

## Product-specific decisions

- Existing **شاي عادي** product #58 continues to exist; **شاي ظرف** is a distinct hidden sale item with one unit of existing SKU `DRK-TEA-AHMAD-BAG` (stock unit `portion`). No duplication of ingredient SKU.
- **سحلب** is created hidden with separate dry-milk and sahleb sachet inventory SKUs, one sachet each. No cost or available-stock quantity assumed.
- **شاي خمير - كأس** is created hidden, without a complex batch processing requirement or artificial per-cup consumption ratio. Must price it and confirm preparation/served volume before it becomes orderable.
- **عرق الريان - كأس كبير** is created hidden for the separate 75ml serving, while existing small glass #84 has its inactive recipe corrected to 40ml. Existing carafes #85/#90 remain untouched.
- Espresso #108: 16g (0.016kg) instead of 18g. Arabic coffee #107: 10g. Cappuccino #109: 18g beans and 150ml milk.
- Vodka cocktail #333 and vodka mojito #334: 75ml per serving. The old mojito rum ingredient remains **inactive and explicitly deprecated** to preserve history, not deleted.
- **شوت دودو** #339: 20ml vodka, one stuffed olive, 20ml freshly prepared lemon juice. The old generic-green-olive line remains inactive and deprecated. Existing Tabasco material #125 remains the correct material, but no two-drop-to-ml conversion is invented: its old inactive 2ml line is clearly marked unverified.
- **3x1** #322: make the existing product available, preserve its already assigned sale price (100). Add one dry 3x1 sachet as an **inactive** draft recipe, retaining the old ground/instant-coffee recipe line inactive and labelled superseded.
- Hookah #362: retain one sellable product; add an **inactive** generic tobacco placeholder and defer brand/flavor-specific recipes; do not add food honey.
- The whiskey cocktail is adjusted to an inactive 75ml component only if the exact existing product name **كوكتيل ويسكي** and inventory name **ويسكي** resolve uniquely.

## Blocking safety boundaries

- All added/updated `ProductRecipeItem` rows stay `is_active=False`.
- Existing reference cost, physical stock balances, opening counts, purchase receipts, supplier payments, product sale price and transaction snapshots are never changed by this command.
- Newly created sale products are hidden/non-orderable with `price_syp=0`, explicitly awaiting a verified price. Do **not** take them live at zero price.
- Staff actor required for `--apply`; logs use `ActivityLog` and are not created by preview.
- Expanding the product menu to additional cocktails, mixed drinks and optional flavors remains a separate review.
- A full application inventory forecast must wait for measured stock counts, validated sales and approved recipes.
