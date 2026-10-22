# Sprint 1 — Operational Data Readiness: first 10–20 products

**Status:** Proposed, read-only prioritization. Neither an approved product list nor a certified stock/cost baseline.

## Source-of-truth and current evidence

- **Current code**: `audit_stock_baseline`, `review_hub_recipe_sheet`, `inventory_stock_count`, `quantity_provenance`, and existing purchasing/receiving screens. This sprint reuses them; no second inventory, recipe, approval, task or costing system.
- **Last known production *aggregate* evidence** (owner-provided read-only query, 20 October 2026): 146 products, 86 available, 283 inventory items, 207 missing reference costs, 208 recipe lines of which 0 active. This cannot identify the current 10–20 leading products or certify their sales, count or invoice values. Do not copy these aggregate values into the current database.
- **Active candidate** means `Product.is_available=True` with food/drink type, or the legacy `OTHER` classification paired with food/beverage. Explicit Internet, event, service and membership entries are excluded; legacy classification is flagged for review. This is not proof that an item is visible or orderable.
- **Pilot selection** ranks by *recent served, non-import-marked order-line units*, then older served lines, then merely configured catalog/recipe evidence and deterministic product ID. Historic monthly imports marked at the line **or** order level never contribute to demand ranking. Unmarked historical aggregates are an acknowledged remaining uncertainty. Served does **not** prove payment or true ingredient consumption.
- Items without sales evidence can fill the 10–20 review slots, but are labelled catalogue fallbacks. No fixed 10–20 names are asserted without querying the actual database.

## Run on a database with current data (read-only)

After verifying the target environment/revision, from the standard Django environment:

```bash
python manage.py audit_stock_baseline --pilot --limit 20 --days 28 --json
```

Use `--limit 10` for a tighter first pass. Only 10–20 requested candidates are permitted in pilot mode; fewer may be returned if the catalog has fewer eligible active entries. The existing `audit_stock_baseline` command **without** `--pilot` retains its original output contract.

The result includes stable product ID/public code, recorded sale price, clearly labelled sales evidence, each recipe line's inventory ID/SKU, unit and draft status, the existing count-provenance state, reference-cost presence, and explicit unresolved review flags. It never exposes private suppliers, actors, cash records or historical purchase correspondence. It does not certify an invoice, auto-populate a receipt, change an order, calculate a forecast, or alter stock. The `pilot_approved` field is always false.

## Review the resulting products in-place

1. Verify that the shortlisted product exists on the actual Hub menu, has the right stable ID, genuine type, price, visibility and orderability. A catalogue fallback is a *candidate*, not a popular product.
2. Match each recipe ingredient to its **existing** inventory ID/SKU and canonical unit. Missing SKU and differing units need deliberate review, not an inferred package size, yield or conversion.
3. Use the **existing** `/staff/inventory/stock-count/` workflow for a real authorized physical count. Its recorded count is not a financial opening balance. If movements were recorded after the count, review their evidence before assuming the current quantity is physically correct.
4. Use `review_hub_recipe_sheet --file <approved review export> --json` for preflight of approved measurements. Trial recipes, offered sugar, hookah variants, chai batches, orange juice extraction and sahleb price conflicts remain held for separate evidence. Do **not** enable recipe lines in bulk.
5. For costs, obtain actual supplier invoice/receipt evidence with supplier, date, currency, size and unit. Existing estimated reference costs are **not** invoice-verified costs; no automatic margin or payable is claimed.
6. Record review notes and decisions in the existing controlled processes. Only after verified evidence and separate authorization should a bounded, id-keyed import preview be contemplated.

## Explicit exclusions and safeguards

- No `--apply`, writes, new schema, forecast, automatic low-stock purchasing, monetary stock valuation or D10 postings.
- Never change order #22/payment #1, historical quantities or recipe activation. No router/network changes.
- Do not invent product rankings from code fixtures. Output reflects the database **where the command is actually run**, not GitHub's repository.
- Production release parity and real staff/devices remain independently gated by [issue #320](https://github.com/Maximad/hub/issues/320); do not claim deployed or pilot accepted on green CI alone.

## Test coverage

- Recent served lines win over a large historic aggregate, including order-level import markers.
- Available physical products only; inactive and service products excluded.
- Stock-count snapshot present vs legacy zero with no snapshot; cost estimate not labelled verified.
- SKU/unit/recipe/sale configuration gaps surfaced without writes.
- Existing command unchanged unless `--pilot` requested; invalid limits and unknown apply switch refused.
- Run full Django/PostgreSQL CI and migration checks prior to review or merge.

## Next decision after the read-only export

Choose a small genuine slice of the 10–20 *from the export*, based on operational importance and staff capacity; approve specific recipe measurements and costs separately. No such selection or financial rule is approved by this PR.
