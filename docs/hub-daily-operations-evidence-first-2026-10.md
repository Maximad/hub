# Hub daily operations: evidence-first pilot (2026-10-20)

## Priority
The owner confirmed that many legacy records were reconstructed from paper and aggregated by hand. They must be retained, but are **not** trusted for demand forecasting, unit costs or stockouts. The primary goal is easy daily execution of **بيع، شراء، جرد، إنترنت**, not completing historical accounting first.

## Release 1: four operational shortcuts, distinct completeness states

- Existing `/staff/` places **sale, purchase, physical count and internet** first for authorized admin/cashier staff. Cash collection and prep remain prominent one-tap secondary links, subject to effective capability checks. Kitchen/waiter roles keep their role-appropriate entry points.
- All cards route to existing POS, Purchase, Stock Count and Internet screens, not duplicate workflows. No POST bypasses existing authorization.
- Existing inventory list shows a **verified physical count flag** only when the authorized staff stock-count action has durable evidence. Historical imported zeros do not count, nor do estimated unit costs.
- A count matching the stored number now appends an immutable audit event plus an idempotency receipt and activity evidence, but **no** stock movement, expense or cash posting. This fixes the historical inability to distinguish a verified zero from an unverified zero.
- Inventory status `مكتملة تشغيلياً` means only: material identity fields are present, and a physical count has been recorded. It does **not** imply final cost verification, an approved financial opening balance, or forecast readiness.
- The existing recipe manager distinguishes sale readiness from ingredient-recipe review: products may be sale-ready while their recipes remain draft, missing or inactive. It no longer displays misleading `0` recipe costs/margins for zero active recipes or missing reference costs.
- Recipe `مفعّلة` means active technical rows, not proof of financial audit. No code here activates inactive recipes or flips the automatic stock deduction flag.
- Existing `review_hub_new_product_prices` and corrected `apply_hub_menu_decisions` are independent and do not run on deployment.

## What this does **not** do

- No database schema changes or historic data rewrites.
- No automatic orders, stock deductions, purchases, receiving, Internet grants, payments or ledger postings.
- No automatic expense classification, waste ledger treatment or stock forecast.
- No changes to MikroTik/RouterOS, provider speeds, walled garden or network profiles.
- No manufactured completion stamps for unverified legacy paper data.
- No automatic low-stock reclassification in this first pilot; that work requires reconciliation of existing threshold-driven tasks against verified counts.

## Staff acceptance checklist

1. Login as an authorized operations user: find sale, purchase, count and Internet immediately on `/staff/`; collection remains directly accessible.
2. Login as kitchen or waiter, or an explicitly denied staff user: no leaked purchase/stock action.
3. Count a genuinely observed material whose recorded value differs: review and confirm, then inspect one audited movement and readiness indicator.
4. Count a genuinely observed zero or a matching quantity: confirm, see readiness indicator, but **no** movement or financial entry; retry same confirmation and verify idempotency.
5. Open the inventory list: uncounted records (including zeros) must remain marked unverified; reference cost must not be described as certified.
6. Open the recipes list: a paid sellable product with a draft recipe must remain sellable but have the separate recipe-warning indicator; no invented 0 margin.
7. On one actual staff/customer device, verify an ordinary sale/collect flow, receiving a genuine purchase and an Internet session without modifying the router.

## Next release: persistent review cases and duty routing

Create a small, auditable `review case` layer for each material/product/recipe where a required evidence item is still unresolved. The current `BusinessDayTask` is the *daily projection*, not another issue ledger. Resolution must follow a relevant operator action or authorized explicit review. Deduplicate case+reason+source, route to checked-in eligible `BusinessDayDutyCoverage` or a visible manager queue, and transfer outstanding work only after accepted handover. Avoid repeated notification for every sale. Then add verified-count-only low-stock suggestions and only later forecast using trustworthy recent operational demand.

## Rollout

Full Django/PostgreSQL CI, role and no-change-audit tests, PR review and merge before any VPS deployment. Deploy through the existing backup/restore verified release script under `deploy` after owner authorization. Production staff acceptance is separate from CI.
