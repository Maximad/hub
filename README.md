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

### Windows PowerShell SSH
```powershell
ssh -i "C:\Users\USER\.ssh\hub_vps" deploy@72.62.52.167
```

### Deploy with Docker Compose
```bash
cd /path/to/hub
cp .env.example .env
# Edit .env with strong secrets and the production domain settings above
docker compose -f docker-compose.prod.yml --env-file .env up -d --build --force-recreate web
docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py migrate
docker compose -f docker-compose.prod.yml --env-file .env exec web mkdir -p /app/staticfiles
docker compose -f docker-compose.prod.yml --env-file .env exec web python manage.py collectstatic --noinput
docker compose -f docker-compose.prod.yml --env-file .env restart web
```

Run `collectstatic` after rebuilding because production previously showed `Missing staticfiles manifest entry for css/hub.css` until static assets were collected.

Traefik should route public HTTP/HTTPS traffic for `hubsweida.jwtalenthouse.com` to the `web` service on port `8000` through the external `proxy` network. The localhost maintenance tunnel remains available at:
- `http://127.0.0.1:8899`

### Post-deploy verification
```bash
curl -I https://hubsweida.jwtalenthouse.com/menu/
curl -I https://hubsweida.jwtalenthouse.com/admin/login/
curl -I https://hubsweida.jwtalenthouse.com/staff/
```

Expected responses:
- `/menu/` → `200`
- `/admin/login/` → `200`
- `/staff/` → `302` to login

Verify Django proxy and CSRF settings in the deployed shell:
```python
from django.conf import settings
print(settings.CSRF_TRUSTED_ORIGINS)
print(settings.SECURE_PROXY_SSL_HEADER)
print(settings.USE_X_FORWARDED_HOST)
```

Expected values:
- `CSRF_TRUSTED_ORIGINS` includes `https://hubsweida.jwtalenthouse.com`
- `SECURE_PROXY_SSL_HEADER` is `("HTTP_X_FORWARDED_PROTO", "https")`
- `USE_X_FORWARDED_HOST` is `True`

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
