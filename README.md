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
This app keeps the localhost tunnel binding for direct maintenance access:
- `127.0.0.1:8899:8000`

The live deployment currently uses:
- Existing Traefik container for public routing
- External Docker network: `proxy`
- Let’s Encrypt resolver: `letsencrypt`
- Public domain: `hubsweida.jwtalenthouse.com`

Required production `.env` values include:
```env
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=hubsweida.jwtalenthouse.com,72.62.52.167,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://hubsweida.jwtalenthouse.com,http://hubsweida.jwtalenthouse.com
```

### Infrastructure access plan

Current target topology is **public Traefik plus optional VPN/private operations access**:

* Public users reach Django through the existing Traefik reverse proxy on the external Docker network named `proxy`; Traefik terminates HTTPS and forwards requests to the `web` service on container port `8000`.
* Direct host access is limited to the existing localhost bind, `127.0.0.1:8899:8000`, for SSH tunnel or same-host maintenance only. Do not expose this bind on `0.0.0.0` unless the firewall and Django host/origin settings are updated intentionally.
* If VPN access is required, prefer **VPN to reverse proxy**: publish a private DNS name through the VPN and route it through Traefik to the same `web` service on the `proxy` Docker network. Use Tailscale/Headscale, WireGuard, or OpenVPN according to the operations team's standard.
* Direct VPN-to-host access may be allowed only for approved admin/staff maintenance clients and should use either a private Traefik router or the localhost tunnel pattern; do not bypass Traefik unless Django proxy/header behavior and CSRF origins are tested.
* The deployment is **IPv4-only by default**. Enable dual-stack IPv4/IPv6 only after the server, Traefik entrypoints, Docker networking, firewall, DNS, and chosen VPN product all support IPv6 end-to-end.

Final access inventory:

* Public URL: `https://hubsweida.jwtalenthouse.com`
* Private VPN hostname: `hub-vpn.internal` placeholder; replace with the real split-DNS name before enabling VPN access.
* Private VPN IP: not assigned by default. If direct access is intentionally allowed, document the approved VPN interface IP here and add it to firewall rules and `DJANGO_ALLOWED_HOSTS`.

Environment guidance for access changes:

* `DJANGO_ALLOWED_HOSTS` must include every hostname or literal IP used by browsers or health checks, including the public domain, any private VPN hostname, and any approved private VPN IP.
* `DJANGO_CSRF_TRUSTED_ORIGINS` must include the full scheme and host for every HTTPS/HTTP browser origin that can submit staff/admin forms, including any private VPN hostname such as `https://hub-vpn.internal`.
* If future trusted-proxy/CIDR hardening is added, document the trusted Traefik container/network CIDR and approved VPN subnets before enabling it. Do not trust arbitrary `X-Forwarded-*` headers from unapproved networks.

Traefik/VPN behavior to verify before enabling VPN access:

* Django is configured to trust `X-Forwarded-Proto` for HTTPS detection and to use `X-Forwarded-Host`; Traefik must continue forwarding `X-Forwarded-Proto` and `X-Forwarded-Host` to the app.
* Confirm client IP handling in Traefik access logs and application logs before relying on IP-based auditing or rate limiting; if exact VPN client IPs are required, configure and test Traefik forwarded-header trust for only the VPN/proxy subnets.
* The app should remain reached through the existing `proxy` Docker network. A separate private Traefik router/entrypoint is needed only if VPN traffic must have a distinct hostname, middleware, TLS policy, or access controls.

Firewall and DNS plan:

* Keep `127.0.0.1:8899:8000` private. Allow external ingress only to public Traefik ports `80/tcp` and `443/tcp`, SSH from approved admin IPs, and the approved VPN interface/subnets.
* Document allowed IPv4 ranges before opening VPN access, for example `100.64.0.0/10` for Tailscale CGNAT or the chosen WireGuard/OpenVPN tunnel CIDR.
* Document allowed IPv6 ranges only if dual-stack is enabled; otherwise do not publish AAAA records and block unsolicited IPv6 ingress.
* Public DNS should point `hubsweida.jwtalenthouse.com` to the public Traefik endpoint. Use private/split DNS for the VPN hostname if needed. Publish AAAA records only after IPv6 works across the server, Traefik, firewall, Docker, and VPN.

Manual verification commands after access or VPN changes:

```bash
# Public HTTPS route
curl -I https://hubsweida.jwtalenthouse.com/menu/

# VPN/private hostname route, after split DNS is configured
curl -I https://hub-vpn.internal/menu/

# Admin login GET
curl -I https://hubsweida.jwtalenthouse.com/admin/login/

# Admin login POST path/CSRF flow; replace credentials only in a secure shell history context
curl -c /tmp/hub-cookies.txt -s https://hubsweida.jwtalenthouse.com/admin/login/ -o /tmp/hub-login.html
python - <<'PY'
from pathlib import Path
import re
html = Path('/tmp/hub-login.html').read_text()
print(re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1))
PY
curl -b /tmp/hub-cookies.txt -c /tmp/hub-cookies.txt -X POST https://hubsweida.jwtalenthouse.com/admin/login/ \
  -H 'Referer: https://hubsweida.jwtalenthouse.com/admin/login/' \
  -d 'username=ADMIN_USER&password=ADMIN_PASSWORD&csrfmiddlewaretoken=CSRF_TOKEN&next=/admin/' -I

# CSRF-protected staff action; authenticate first, then use a safe test item/session in staging when possible
curl -b /tmp/hub-cookies.txt -c /tmp/hub-cookies.txt -X POST https://hubsweida.jwtalenthouse.com/staff/kitchen/item/ITEM_ID/status/ \
  -H 'Referer: https://hubsweida.jwtalenthouse.com/staff/kitchen/' \
  -d 'status=accepted&csrfmiddlewaretoken=CSRF_TOKEN' -I

# Static and media assets
curl -I https://hubsweida.jwtalenthouse.com/static/css/hub.css
curl -I https://hubsweida.jwtalenthouse.com/media/products/normal-tea.png
```

### Windows PowerShell SSH
```powershell
ssh -i "C:\Users\USER\.ssh\hub_vps" deploy@72.62.52.167
```

### Deploy with Docker Compose
```bash
cd /opt/hub

git fetch origin
git pull --ff-only origin main

mkdir -p /opt/hub/media/products
mkdir -p /opt/hub/staticfiles

docker compose -f docker-compose.prod.yml --env-file .env up -d --build --force-recreate web

docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py migrate

docker compose -f docker-compose.prod.yml --env-file .env exec web mkdir -p /app/staticfiles

docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py collectstatic --noinput --clear

docker compose -f docker-compose.prod.yml --env-file .env restart web
```

Run `collectstatic` after rebuilding because production previously showed `Missing staticfiles manifest entry for css/hub.css` until static assets were collected. Production Compose also mounts `./staticfiles:/app/staticfiles` so collected assets persist across container recreation.

Traefik should route public HTTP/HTTPS traffic for `hubsweida.jwtalenthouse.com` to the `web` service on port `8000` through the external `proxy` network. The localhost maintenance tunnel remains available at:
- `http://127.0.0.1:8899`

After deployment, verify the production routes:

```bash
curl -I https://hubsweida.jwtalenthouse.com/menu/
curl -I https://hubsweida.jwtalenthouse.com/media/products/normal-tea.png
curl -I https://hubsweida.jwtalenthouse.com/admin/login/
```

Expected results:
- `/menu/` returns `200`.
- `/media/products/normal-tea.png` returns `200` with `image/png`.
- `/admin/login/` returns `200`.

Also verify public menu and staff POS product images/placeholders are 2:3 portrait, centered horizontally and vertically, not distorted, static CSS still loads, Traefik domain routing works, and `hub-web` is attached to both `hub_default` and `proxy` networks.

### Persistent product media

Product images are served from the host's persistent media folder and mounted into the Django container:

```bash
mkdir -p /opt/hub/media/products
```

When deploying from `/opt/hub`, production Docker Compose mounts `./media:/app/media`, so the container path for product images should be:

```text
/app/media/products
```

Public product image URLs use the `/media/` prefix, for example:

```text
/media/products/normal-tea.png
/media/products/morning-combo.png
/media/products/tea-with-lemon.png
/media/products/zhourat.png
/media/products/tea-with-sage.png
/media/products/quieche.png
/media/products/full-quieche.png
```

From Windows PowerShell, upload product images to the VPS with:

```powershell
scp -i "C:\Users\USER\.ssh\hub_vps" "C:\Users\USER\Desktop\product-images\*" deploy@72.62.52.167:/opt/hub/media/products/
```

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
