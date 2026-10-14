# Hub Suite — Role-aware first-week workspace actions
2026-10-14 · P0 UI-only implementation

## Why
The Operations home already supports visits, orders, cashier drawers, Internet
and a shift summary. Users struggled to find routine work among account cards,
secondary administrative links and the full header. This change keeps the
existing surface but moves the **next permitted task** to the top of the page.

## Behavior
- `/staff/` shows **ابدأ من هنا** before the search/active account list, with
  at most four prominent action cards, touch-friendly on phones. Descriptions
  tell staff what each link does, not the internal app-module name.
- Additional *permitted* actions are available under the native expandable
  **مهام أخرى حسب صلاحياتك**. Existing `#staff-today` and `#staff-more`
  links, accounts, orders, cashier drawer, close-day and Internet behavior stay.
- The shared shell navigation is unchanged. No JS is required for the cards.
- Full admin sees: **New order, collect payments, stock count, purchase**
  followed by additional permitted expense/preparation/Internet tasks.
- The **تشغيل وكاشير** effective role prioritizes **cashier, order, count,
  purchase**, with expense entry still accessible in secondary tasks.
- Kitchen/bar staff without POS rights prioritize **preparation, team tasks,
  view inventory**. No purchase shortcut, collection or count by virtue of the
  generic inventory capability.
- Waiter/service staff see **new order, team tasks, visits and reservations**
  according to effective permissions.
- Explicit `inventory_stock_count` grants add a physical-count shortcut.
  Explicit denial removes it even for a cashier account. The purchase shortcut
  still requires **both** inventory capability and admin/cashier role, matching
  the current view's additional role restriction.
- An Open Account dialog button and dialog are shown only when the user has
  both `orders` and `visit_manage` permissions. This avoids advertising a
  dialog that would be refused by the POST route.

## Security / operational boundaries
- The quick-start lists contain only **allowlisted existing Django routes**.
  Every URL still checks server-side permissions. Displaying a link does not
  grant any new capability.
- No new customer/order/stock/payment operations or migrations, no changes
  to payment posting, D10 waste accounting, automatic inventory deduction,
  pricing, currency conversion or MikroTik.
- Existing view-as/read-only safeguards continue to govern POST operations.
- This is navigation-only and read-only; rendering the page writes no records.

## Acceptance / rollback
1. Full Django/PostgreSQL CI and role/override tests must pass before merge.
2. Merge into `main` only when checks are green; do not deploy during this
   development batch.
3. Later staff pilot: use real low-end phones/tablets and the actual role
   accounts to locate order, collect, prep, stock count, purchase and expenses.
4. A full-page fallback still reaches all original routes. An individual
   permission denial must remove the corresponding shortcut.
5. If device feedback rejects the hierarchy, revert the single UI PR without
   a database migration or financial data modification.
