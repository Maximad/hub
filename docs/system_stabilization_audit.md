# Hub/Masharib System Stabilization Audit

## What was fixed

- Restored Django's default file storage backend while preserving the existing WhiteNoise staticfiles backend, `MEDIA_URL`, and `MEDIA_ROOT`.
- Reduced admin home clutter by keeping product media and low-level technical records registered for direct links/autocomplete while hiding them from the main admin index where possible.
- Added read-only audit/smoke tooling that does not modify operational data.

## What was tested

- Critical Django admin pages for products, media assets, orders, payments, settings, finance, inventory, events, and vendors.
- Media upload and external URL creation.
- Product media assignment and menu/POS rendering without product images.
- Finance safety behavior around overpayments, negative payments, discounts, cancelled orders, and cashbox-affecting expenses.
- Inventory safety behavior around draft purchases, received stock movements, positive movement quantities, waste reasons, default stock deduction, and products without recipes.

## Admin simplification decisions

- The normal admin workflow for product images is the Product admin inline.
- The main media entry is **مكتبة الوسائط** for general upload/selection.
- ProductMedia remains technically registered so existing links and autocomplete integrations continue to work, but it is hidden from the admin home to avoid a confusing second image library.
- Notification recipients/logs, activity logs, order items, purchase items, stock movements, and production batch ingredients are treated as technical records rather than routine admin destinations.

## How media should be managed

1. Upload reusable files from **Catalog → مكتبة الوسائط** or from the popup selector in the Product media inline.
2. Open a Product in admin and use the media inline to select/upload media.
3. Set one active primary image.
4. Use the inline flags to control visibility on the public menu and POS.
5. Uploaded files are stored below `MEDIA_ROOT`; existing `/media/products/...` paths are preserved.

## Smoke checks

Run:

```bash
python manage.py smoke_check
```

The command reports `PASS`, `WARN`, or `FAIL` for public and staff routes. `200`, `302`, and `403` are acceptable. `500` fails. Unexpected `404` is reported as a warning.

## System audit

Run:

```bash
python manage.py system_audit
```

The command is read-only. It reports potentially risky data such as products without sections, missing media files, invalid totals, negative inventory, broken notification context, and delivery orders missing contact/address details.

## Known remaining issues

- Some warnings from `system_audit` may reflect intentionally incomplete setup data in fresh/demo databases.
- Low-level stock movement application is intentionally powerful; operational double-receive prevention is handled by the purchase receive workflow checking existing stock movements/received state.
- The smoke command does not authenticate as staff; redirects/403s are acceptable and expected for protected staff routes.

## Recommended daily workflow

1. Run `python manage.py smoke_check` after deployment and before opening service.
2. Run `python manage.py system_audit` daily or before inventory/finance reconciliation.
3. Manage product images from Product admin inlines; use **مكتبة الوسائط** for reusable uploads.
4. Review admin-visible finance and inventory records; avoid direct edits to technical logs/movements unless reconciling a documented issue.

## Deployment notes

- Ensure persistent storage is mounted at `MEDIA_ROOT`.
- Run `python manage.py check` and `python manage.py collectstatic --noinput` during deployment.
- No existing data, media, settings, users, products, orders, payments, inventory, expenses, purchases, or uploaded files should be deleted by these changes.
