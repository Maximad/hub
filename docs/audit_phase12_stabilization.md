# Phase 12 Stabilization Audit

## Summary
- Created branch `phase12-stabilization-audit` from the only available local branch. Fetch from `origin/main` was attempted, but this checkout has no `origin` remote configured.
- Reproduced the known `/admin/core/product/` failure path by rendering the Product admin with a Django test client and `DEBUG_PROPAGATE_EXCEPTIONS=True`.
- Fixed two high-confidence live-operation bugs: Product admin margin columns and the product margin report both referenced missing imports.
- Added focused regression coverage for Product admin list/detail rendering with missing cost/media/recipe, Product margin report rendering with missing cost, and Payment model overpayment validation.
- No migrations were added. No data, media, settings, products, orders, users, payments, reports, inventory, sessions, delivery data, notifications, receipts, or uploaded files were deleted or reset.
- No binary files were added.

## Critical issues found

### `/admin/core/product/` 500
- Exact traceback cause: `NameError: name 'product_unit_margin' is not defined` from `core/admin.py` while rendering Product admin `list_display` methods `estimated_margin_display`, `estimated_margin_percent_display`, and `cost_warning`.
- Cause: Product admin used `product_unit_margin()` without importing it from `core.services.margins`.
- Fix: added the missing import only.

### `/staff/reports/products/` 500
- Exact traceback cause from URL smoke test: `NameError: name 'Category' is not defined` at `core/views_legacy.py`, inside `_product_margin_context()`.
- Cause: `_product_margin_context()` queries `Category.objects.order_by('name_ar')`, but `Category` was not imported.
- Fix: added `Category` to the existing `core.models` import.

## Issues fixed in this PR
- Product admin list now loads with products missing cost/media/recipe.
- Product admin detail now loads with products missing cost/media/recipe.
- Product margin report now loads when products have no estimated cost.
- Payment model validation now rejects active, non-reversed, non-UNPAID payments that exceed the order's remaining balance.

## Known remaining issues
- The local default settings point to PostgreSQL host `db`; that host is not resolvable in this environment. DB-backed checks were run against a temporary SQLite settings module after applying migrations.
- There is no configured `origin` remote in this checkout, so the requested latest-main fetch could not be completed.
- URL smoke coverage used an empty migrated local test database and superuser session. It verifies route rendering does not 500, but it does not validate production data edge cases.
- Notification grouping code already groups visible rows by event/group key and uses Web Audio comments/no binary audio. No additional notification code change was made in this PR.

## Commands run
- `git fetch origin main` — failed because no `origin` remote is configured.
- `git checkout -b phase12-stabilization-audit`.
- `python manage.py check` — passed.
- `python manage.py makemigrations --check --dry-run` — no model changes detected; emitted a warning because PostgreSQL host `db` could not be resolved for migration-history consistency.
- `PYTHONPATH=/tmp:$PYTHONPATH python manage.py makemigrations --check --dry-run --settings=sqlite_settings` — passed, no changes detected.
- `python manage.py collectstatic --noinput` — passed.
- `python -m compileall core catalog accounts events vendors members internet reservations` — passed.
- `rg -n "^(<<<<<<<|=======|>>>>>>>)"` — no merge conflict markers found.
- `find . -type f \( -name "*.mp3" -o -name "*.wav" -o -name "*.ogg" -o -name "*.ttf" -o -name "*.otf" \) -print` — no matching audio/font binary files found.
- `PYTHONPATH=/tmp:$PYTHONPATH python manage.py test core.tests.test_phase12_stabilization --settings=sqlite_settings -v 1` — passed.
- `PYTHONPATH=/tmp:$PYTHONPATH python manage.py migrate --noinput --settings=sqlite_file_settings` — passed in local SQLite audit database.
- `PYTHONPATH=/tmp:$PYTHONPATH python manage.py showmigrations --plan --settings=sqlite_file_settings` — all migrations applied in the local SQLite audit database.
- Django test-client URL smoke script against `sqlite_file_settings` — passed for audited URLs listed below.

## Pages tested
All tested URLs returned 200 or 302; none returned 500.

- `/menu/` — 200
- `/admin/login/` — 302
- `/staff/` — 200
- `/staff/orders/` — 200
- `/staff/cashier/` — 200
- `/staff/pos/` — 200
- `/staff/reports/` — 200
- `/staff/close-day/` — 200
- `/staff/qr/` — 200
- `/staff/qr/print/` — 200
- `/staff/menu-tools/` — 200
- `/staff/members/` — 200
- `/staff/internet/` — 200
- `/staff/wifi/` — 200
- `/staff/events/` — 200
- `/staff/reservations/` — 200
- `/staff/vendors/` — 200
- `/staff/food-lab/` — 200
- `/staff/modifiers/` — 200
- `/staff/prep/` — 200
- `/staff/prep/kitchen/` — 200
- `/staff/prep/bar/` — 200
- `/staff/notifications/` — 200
- `/staff/delivery/` — 200
- `/staff/finance/` — 200
- `/staff/finance/expenses/` — 200
- `/staff/finance/cashbox/` — 200
- `/staff/inventory/` — 200
- `/staff/inventory/items/` — 200
- `/staff/inventory/purchases/` — 200
- `/staff/inventory/movements/` — 200
- `/staff/reports/products/` — 200
- `/admin/core/product/` — 200

## Admin pages tested
- Product admin changelist — passed with missing estimated cost, no media, and no recipe.
- Product admin detail — passed with missing estimated cost, no media, and no recipe.

## Data integrity findings
- No production data was available because the configured PostgreSQL host was unreachable in this environment.
- On the migrated local SQLite audit database, there were no existing products, orders, payments, purchases, movements, expenses, or notifications beyond test-created rows.
- Static code review confirms financial totals clamp remaining order balance to zero, discounts are capped against order totals, and payment creation view blocks overpayment. This PR adds model-level validation as a second defensive layer.

## Migration audit
- No migrations were created in this PR.
- `makemigrations --check --dry-run` reported no pending model changes.
- Local SQLite migration plan showed all migrations applied after `migrate`.
- Recent Phase 12 migrations appear additive in the local application path; no destructive migration was introduced here.

## Notification audit result
- Notification display currently groups recipient rows through `group_notification_recipients_for_user()` and stable group keys.
- Unread count is calculated from grouped unread cards.
- Mark-as-read updates all recipient rows for the selected group.
- Sound is implemented via Web Audio/no binary audio assets.
- No notification code change was made because the grouping safeguards are already present and no 500 was reproduced there.

## Finance/inventory audit result
- Payment overpayment is blocked in the cashier view and now also in `Payment.clean()`.
- Inventory deduction is gated by system settings and stock deduction mode.
- Purchase/expense/cashbox logic was reviewed at a high level; no destructive changes were made.

## Recommended next fixes
- Run the data integrity query set against a production database snapshot or staging clone with real Phase 12 data.
- Add a reusable management command for operational data integrity reporting so it can run before deploys.
- Add URL smoke tests to CI using the same route list.
- Add focused notification grouping tests with multiple recipients and multiple prep items for the same order/station.
- Add model/service-level payment and discount invariant tests for cancelled orders, reversed payments, delivery fees, and partial-payment workflows.

## Production deployment notes
- Do not deploy directly from this audit environment.
- No migration step is required for this PR.
- Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --plan`, and targeted admin smoke tests against staging before production.
- Confirm `/admin/core/product/` and `/staff/reports/products/` render against a copy of live data before release.
