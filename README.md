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
