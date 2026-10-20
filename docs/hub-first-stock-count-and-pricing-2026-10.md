# Hub Sweida — first product-pricing and physical-stock pilot

## Scope and evidence

PR #323 was applied on production with 18 creations, 13 updates and two unchanged decisions. A read-only repeat showed only one pending update for product 322, Nescafe 3x1: its old POS visibility flags had not persisted. The correction to the product visibility synchronization is included in this PR.

The new products were created hidden and non-orderable with no approved prices. This PR does not invent any sale price, enable stock deduction, post accounting entries or create a second physical count subsystem.

## 1. Repair the existing 3x1

For ordinary products the existing pre-save signal makes the menu/QR flags canonical and synchronizes the historical POS flags from them. The previous release modified the historical flags directly. The repair updates canonical flags and persists both sets, so 3x1 becomes orderable in both the public menu and staff POS.

After green full CI, approved merge, normal deploy and verified backup, run the original decision command without apply:

    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py apply_hub_menu_decisions

Expected: only the known product-322 availability fix. Stop and investigate any other proposed mutation. After explicit review, apply as an authorized named Django staff actor and repeat the preview. The target is zero updates.

## 2. Approve new sale prices separately

The new review_hub_new_product_prices command is restricted to the four recently created products, resolved by their stable metadata keys:

- sahleb — سحلب
- tea_bag — شاي ظرف
- tea_khamir_glass — شاي خمير - كأس
- arak_large_glass — عرق الريان - كأس كبير

Export the fill-in template on VPS, using the deployed release:

    cd /opt/hub
    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py review_hub_new_product_prices --template \
      > /tmp/hub-new-menu-prices.csv

Edit the file in UTF-8 while preserving its columns. Give each approved entry a positive integer price in new Syrian lira. Set publish to yes or no explicitly. A CSV can contain only the products approved this time. Values are never inferred from historic prices or costs. The expected_price_syp field protects against stale edits. Editing a live item's sale price or silently unpublishing a live item is refused.

Transfer the reviewed CSV to the already running web container and preview:

    docker cp /tmp/hub-new-menu-prices.csv hub-web:/tmp/hub-new-menu-prices.csv
    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py review_hub_new_product_prices \
      --file /tmp/hub-new-menu-prices.csv

Preview rolls back the entire batch. If approved, run with the explicit apply flag and an active authorized staff username:

    docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
      python manage.py review_hub_new_product_prices \
      --file /tmp/hub-new-menu-prices.csv --apply --actor maximad

Publishing yes makes the product available on the general menu AND POS. Publishing no saves the price but leaves it hidden. All recipe lines remain inactive and nothing is deducted from inventory. Repeating the preview should show unchanged after a successful apply.

## 3. Use the EXISTING stock count screen, no duplicate workflow

Go to the existing staff stock-count screen:

    /staff/inventory/stock-count/

It is reachable via المخزون → جرد المخزون, restricted by the inventory_stock_count capability. Search the ingredient, count physically, select its actual unit, provide a meaningful correction reason (e.g. جرد تشغيلي افتتاحي), preview, then confirm. The existing implementation creates a single auditable StockMovement.CORRECTION when quantities differ and does not post a purchase, expense, payment or opening valuation.

Prioritize an initial limited sample:
- DRK-COF-ARABIC: بن عربي, kg
- DRK-COF-ESPRESSO: بن إسبريسو, kg
- DRK-TEA-AHMAD-BAG: شاي ظرف, portion
- ING-DAIRY-MILK: حليب, liter
- DRK-MATE-YERBA: متة, kg
- DRK-ALC-VODKA-SMIRNOFF: فودكا, liter
- DRK-ALC-ARAK-RAYAN: عرق, liter
- DRK-COF-3IN1-SACHET: 3x1, piece
- DRK-SAHLEB-WIN-SACHET: ظرف سحلب, piece
- DRK-MILK-POWDER-SACHET: ظرف حليب ناشف, piece
- ING-PICKLE-STUFFED-OLIVE: زيتونة محشية, piece
- ING-PREP-FRESH-LEMON-JUICE: عصير ليمون طازج, liter

An existing zero quantity is not evidence of a real stockout. Zero is appropriate only after an actual count. Record raw whole lemons in kg separately from actual measured liters of squeezed juice; do not guess an extraction ratio. Do not count or activate the inactive hookah tobacco placeholder until actual brand/flavor inventory is identified.

A physical count correction is operational evidence, not a financially valued opening stock. Financial valuation and automated consumption remain separately gated. Do not enable recipes in bulk.

## Release safety

- Use the deploy user and the normal verified backup/restore deploy script; preserve operational records.
- Compare the expected Git release SHA with backups/last_deployed_revision.txt before running new commands.
- Both price preview and the menu-decision preview make no persistent changes.
- No schema migration, RouterOS, MikroTik, provider changes, inventory mass import, supplier payment or finance rollout flag is included.
- After real staff acceptance, record PASS/FAIL for product availability, physical counts, permission enforcement and currency display.
