# Staff inventory units: entry units versus stored base units

The base unit `InventoryItem.unit` and historical stock movements are unchanged.
Staff may enter only known physical conversions:

- غ / كغ (1 كغ = 1000 غ)
- مل / لتر (1 لتر = 1000 مل)

Other units (piece, pack, box, bottle, portion, other) remain base-unit-only.
There is **no** assumed number of pieces per box or bottle size.

Entry surfaces:
- **Inventory purchase drafts:** quantity and **price per selected entry unit**.
  Store quantity in base units and unit price in base-unit terms. Existing schema
  supports three decimal places of quantity and **two** for prices. If the
  converted price requires greater precision, reject with guidance instead of
  rounding and silently altering invoice totals. Payment and receipt remain
  separate steps.
- **Receipt screens (Inventory and Finance):** choose unit per line, show the
  equivalent base quantity, convert before calling the existing idempotent,
  permission-gated posting service. Never bypass remaining-quantity checks.
- **Physical counts:** choose entry unit; preview the converted base quantity
  and signed difference before confirmation. Stored correction remains in the
  item's base unit and carries no automatic financial valuation.

Server validation is authoritative. Keep existing no-unit POST requests
backward-compatible: they mean the item's stored base unit. Do not infer entry
unit from a numeric value. No historical migrations, money changes, or RouterOS
changes are part of this batch.

Limits: manually entered opening balance, recipes, waste and direct admin edits
still use their existing item base units. Before extending those workflows, test
their respective posting/recipe implications. Units like cases/boxes require an
**item-specific packaging factor** with separate policy and tests.
