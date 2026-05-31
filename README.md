# Hub / Masharib - Phase 1

نظام إدارة مقهى **مشاريب** (Arabic-first) مبني بـ Django + PostgreSQL + HTMX.

## Phase 1 Features
- Arabic-first RTL UI.
- Custom user model from day one.
- Core operational models (rooms, tables, catalog, orders, payments, members, internet, shifts, activity log).
- Django Admin enabled.
- Bootstrap command for Phase 1.5+ safe Arabic setup data.
- Dockerized local/dev and production setup.

## Stack
- Python 3.12
- Django
- PostgreSQL only (no SQLite)
- Gunicorn
- Server-rendered templates + HTMX

## Quick Start (Local)
1. Copy env file:
   ```bash
   cp .env.example .env
   ```
2. Run containers:
   ```bash
   docker compose up --build
   ```
3. In another shell, create superuser:
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```
4. Run bootstrap data setup (recommended):
   ```bash
   docker compose exec web python manage.py bootstrap_masharib
   ```

## VPS Deployment (Production)
This app is designed **not to expose public ports** and to bind only localhost:
- `127.0.0.1:8899:8000`

### Windows PowerShell SSH
```powershell
ssh -i "C:\Users\USER\.ssh\hub_vps" deploy@72.62.52.167
```

### Deploy with Docker Compose
```bash
cd /path/to/hub
cp .env.example .env
# Edit .env with strong secrets
docker compose -f docker-compose.prod.yml up -d --build
```

Your existing gateway/proxy should route to:
- `http://127.0.0.1:8899`

## Management Commands
- Primary setup command (recommended):
  ```bash
  python manage.py bootstrap_masharib
  ```
- Legacy wrapper (deprecated, delegates to bootstrap):
  ```bash
  python manage.py seed_demo_ar
  ```

## Apps
- `core`, `accounts`, `catalog`, `locations`, `orders`, `payments`, `members`, `internet`, `reports`, `audit`

## Phase 1.5 Flex Model
- Public `MenuSection` display is intentionally separate from internal `Product` operational logic.
- Products can be classified with `item_type`, subtypes, tags, stations, availability, and vendor/event/member rules without forcing customer menu layout.

### Run bootstrap (idempotent)
```bash
docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py bootstrap_masharib
```

### Optional staff password
```bash
docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py bootstrap_masharib --default-password "CHANGE_ME"
```

Bootstrap updates structural/reference data with `update_or_create` and does not delete real orders, payments, or manual production data.

## Phase 2 Public QR Menu
اختبار روابط الزبائن:
- `/menu/`
- `/menu/table/<qr_token>/`
- `/order/<public_code>/`

## Phase 3 Staff Ops
اختبار روابط الفريق:
- `/staff/orders/`
- `/staff/cashier/`


## Phase 4 Menu & QR Operations
اختبار روابط الفريق الجديدة:
- `/staff/`
- `/staff/qr/`
- `/staff/qr/print/`
- `/staff/menu-tools/`

## Phase 5 Operational Safety & Reports
اختبار روابط التشغيل اليومية:
- `/staff/reports/`
- `/staff/reports/day/`
- `/staff/close-day/`
- `/staff/reports/day.csv`


## Phase 6 Memberships & Internet Sessions
اختبار الروابط الجديدة:
- `/staff/members/`
- `/staff/internet/`
- `/staff/wifi/`

## Phase 7 Events, Reservations, Vendors & Food Lab
اختبار الروابط الجديدة:
- `/staff/events/`
- `/staff/events/new/`
- `/staff/events/<event_id>/`
- `/staff/reservations/`
- `/staff/reservations/new/`
- `/staff/reservations/<reservation_id>/`
- `/staff/reservations/<reservation_id>/status/`
- `/staff/vendors/`
- `/staff/vendors/new/`
- `/staff/vendors/<vendor_id>/`
- `/staff/vendors/<vendor_id>/participation/new/`
- `/staff/food-lab/`

## Phase 8 – Hub / Masharib UI redesign
- Added shared design system stylesheet at `static/css/hub.css` with reusable tokens and components.
- Updated base template and key staff/public templates to use responsive card-based RTL layout.
- CSS variables allow future theme customization (colors, fonts, radius, shadows) from one place.
- No schema/model changes, no migrations added, and existing routes preserved.

## Phase 11A — Staff bulk import tools

Bulk import tools are available for managers at `/staff/import/`. Access is restricted to superusers and users with `role=admin`; other staff are redirected back to the staff dashboard with an Arabic permission message.

### Routes

* `/staff/import/` — import dashboard.
* `/staff/import/<import_type>/` — upload page.
* `/staff/import/<import_type>/template.csv` — UTF-8 CSV template download.
* `/staff/import/<import_type>/preview/` — POST upload preview and row validation.
* `/staff/import/<import_type>/confirm/` — POST confirmation and save.

### Supported import types

* `products` — products, sections, tags, modifier group assignments, prep station lookup, and primary media URL. The CSV `product_type` maps to the existing `Product.item_type` field; `code` is matched against a UUID `public_code` when possible and is otherwise stored as `metadata.import_code` for future product imports. Missing tags are warnings and are not created automatically. Missing sections are created only when a product is saved in a create/update mode.
* `events` — events with safe date parsing. The current event model has no direct price field, so `price_syp` creates or updates a default `EventTicketType` named `تذكرة أساسية`.
* `vendors` — vendor partners. The current vendor model stores `name` as `name_ar`, `contact_name` as `contact_person`, `category` as `vendor_type`, and `notes` as `settlement_notes`; email is validated as a warning because the model has no email field.
* `members` — members and optional opening ledger entries. The current member model has no email or notes fields, so those columns are accepted without crashing and shown as warnings. `membership_plan` matches `MembershipPlan` by code or name. Opening minutes/credit create `MemberCreditLedger` entries when provided.
* `internet-packages` — maps directly to the existing `InternetPackage` model (`minutes` maps to `duration_minutes`). The current model has no `is_active` field, so false values are warnings.
* `tables` — table/QR areas. `qr_token` may be supplied as a UUID or left empty so the existing `TableArea` default generates a safe token. `room_or_area` creates or reuses a `Room`; numeric database IDs are not exposed.

### CSV notes

* Use CSV encoded as UTF-8 (templates are emitted with a UTF-8 BOM for Excel compatibility).
* Boolean columns accept `true/false`, `yes/no`, `1/0`, and `نعم/لا`.
* Whitespace is stripped from all cells.
* Missing columns, malformed dates, invalid prices, invalid booleans, and invalid numbers are reported as row-level or file-level Arabic errors instead of crashing.
* Import modes are `dry_run`, `create_only`, `update_only`, and `create_and_update`.
* Nothing is saved if validation errors exist. Warnings can be saved only after explicit confirmation.

### Deployment note

No schema migrations are required for Phase 11A. Run and test imports on staging before using them in production, especially for duplicate matching and product modifier applicability.

## Phase 11C — Manual internet/workspace session billing

Staff internet/workspace billing is managed from `/staff/internet/`, with session details at `/staff/internet/session/<id>/`. This phase is **manual operational billing only**: staff starts and ends sessions in Hub/Masharib, and the system calculates duration and price. It does not call router APIs, disconnect devices, meter traffic, provision vouchers, or automate MikroTik/UniFi/RADIUS/captive-portal access.

### Billing modes

* **Prepaid/package sessions** (`prepaid`): use a configured member minute balance where available. Ending a prepaid member session deducts the actual duration from the active subscription and creates a member ledger entry. If the active member balance is missing or insufficient, finalization is blocked with an Arabic warning so balances never become negative.
* **Open metered sessions** (`open_metered`): staff starts the session when the guest/member begins using internet or workspace time and ends it when they leave. The total is calculated from elapsed minutes, hourly rate, minimum billable minutes, grace minutes, and optional daily cap.
* **Free/manual sessions** (`free`, `manual`): free sessions calculate zero. Manual overrides are available only to admins/superusers and require an override reason.

### Internet/workspace settings

Admin settings include an Arabic fieldset named `إعدادات الإنترنت والعمل` for defaults such as billing mode, hourly rate, minimum minutes, grace minutes, daily cap, guest/member enable flags, automatic order creation, required guest phone, and the optional internet service product.

When order conversion is used, configure **Internet service product** to an existing `Product` that represents internet/workspace billing. The session order uses that product, writes the session duration and customer/member name in the order note, stores the linked order on the session, and then appears normally in cashier. Payments are created only through an order so cashier totals remain consistent.

### Automation roadmap

1. **Manual start/end billing** — current Phase 11C behavior.
2. **Voucher/access-code captive portal** — future phase for access-code workflows.
3. **Router integration** — future MikroTik/UniFi/RADIUS integration. Placeholder fields such as provider, access code, network session ID, MAC, IP, bandwidth profile, and network status are optional and not required in the UI yet.

### Current permissions note

The existing staff capability map keeps internet/member operations limited to `admin` and `cashier` roles (plus superusers). Waiter access can be widened in a later role-policy pass without changing the billing data model; kitchen users remain excluded from internet billing management.

## Phase 11E — Kitchen / prep-station workflow

### Routes

* `/staff/kitchen/` — dedicated Arabic RTL kitchen/prep board.
* `/staff/kitchen/partial/` — HTMX polling partial used by the board every ~7 seconds.
* `/staff/kitchen/order/<public_code>/` — lightweight prep-focused order detail.
* `/staff/kitchen/item/<item_id>/status/` — CSRF-protected POST endpoint for item preparation status changes.

### Role access

* Superusers and `admin` users have full kitchen access.
* `kitchen` users can access the kitchen/prep board from `/staff/` and update preparation statuses only; they are not given cashier, reports, imports, settings, or user-management access.
* `cashier` and `waiter` users can view the board; serving is available to cashier/waiter/admin where permitted, while cancellation is limited to admin/cashier.
* Anonymous users are redirected through the normal login-required staff flow.

### Item preparation status workflow

Order items now track an item-level preparation status:

1. `pending` — جديد
2. `accepted` — تم الاستلام
3. `preparing` — قيد التحضير
4. `ready` — جاهز
5. `served` — تم التسليم
6. `cancelled` — ملغي

Kitchen users can move items through `pending → accepted → preparing → ready`. Cashier/waiter/admin users may mark ready items as served where their role allows, and admin/cashier users may cancel an active item. Invalid transitions return an Arabic message instead of a server error. Order-level status remains manual/conservative so the existing staff order board and cashier workflow are not changed unexpectedly.

### Prep-station grouping

The board uses the existing `catalog.PrepStation` model and the existing `Product.prep_station_ref` field. Items are grouped by their product prep station; products with no prep station appear under `غير محدد / عام`. Prep stations are manageable in Django admin with Arabic/English names, active flag, and sort order.

### Manual testing checklist

1. Admin can access `/staff/kitchen/`.
2. Kitchen user can access `/staff/kitchen/`.
3. Kitchen user cannot access cashier/reports/users/imports/settings.
4. Waiter creates order from POS.
5. New order appears on kitchen board.
6. Item modifiers appear clearly.
7. Item notes appear clearly.
8. Kitchen changes item status `pending → accepted`.
9. Kitchen changes `accepted → preparing`.
10. Kitchen changes `preparing → ready`.
11. Ready item appears under the ready filter and is hidden from the default active filter.
12. Cancelled orders do not appear as active prep.
13. Delivery orders show a delivery badge/note context but no payment information.
14. Table orders show the table label.
15. No long UUIDs are used as primary kitchen order labels.
16. Staff orders/cashier still work.
17. Public menu still works.
18. No 500s in logs.
