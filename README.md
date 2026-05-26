# Hub / Masharib - Phase 1

نظام إدارة مقهى **مشاريب** (Arabic-first) مبني بـ Django + PostgreSQL + HTMX.

## Phase 1 Features
- Arabic-first RTL UI.
- Custom user model from day one.
- Core operational models (rooms, tables, catalog, orders, payments, members, internet, shifts, activity log).
- Django Admin enabled.
- Demo seed command in Arabic.
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
4. Seed demo Arabic data:
   ```bash
   docker compose exec web python manage.py seed_demo_ar
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
- Seed Arabic demo data:
  ```bash
  python manage.py seed_demo_ar
  ```

## Apps
- `core`, `accounts`, `catalog`, `locations`, `orders`, `payments`, `members`, `internet`, `reports`, `audit`
