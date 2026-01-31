# Mizan Backend

Backend API and services for **Mizan AI** — a restaurant operations platform (scheduling, staff, attendance, POS, notifications, and more). Built with Django, Django REST Framework, Daphne (ASGI), Celery, and Redis.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Docker](#docker)
- [Production Deployment](#production-deployment)
- [API Overview](#api-overview)
- [Integrations](#integrations)
- [Scripts & Commands](#scripts--commands)

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Framework** | Django 5.x |
| **API** | Django REST Framework, drf-spectacular (OpenAPI/Swagger) |
| **Auth** | JWT (django-rest-framework-simplejwt), custom email backend |
| **Server** | Daphne (ASGI) — HTTP + WebSockets |
| **Task queue** | Celery + Redis |
| **Database** | PostgreSQL |
| **Real-time** | Django Channels, Redis channel layer |
| **Integrations** | Firebase (push), WhatsApp Cloud API, Stripe, Square POS, Lua Agent |

---

## Repository Structure

```
mizan-backend/
├── mizan/                    # Project config
│   ├── settings.py           # Django settings (DB, CORS, JWT, Celery, etc.)
│   ├── urls.py               # Root URL routing → app urls
│   ├── asgi.py               # ASGI app (HTTP + WebSocket routing)
│   ├── wsgi.py               # WSGI (e.g. for gunicorn if needed)
│   ├── celery.py             # Celery app bootstrap
│   └── routing.py            # (Optional) extra ASGI routing
│
├── accounts/                 # Auth, users, restaurant, invitations
│   ├── models.py             # CustomUser, Restaurant, StaffInvitation, etc.
│   ├── views.py              # Login, register, JWT, password reset
│   ├── views_agent.py        # Lua agent: context, accept-invitation, lookup
│   ├── urls.py               # /api/ → auth, register, staff, restaurant, agent
│   └── ...
│
├── attendance/               # Attendance (clock in/out, reports)
├── dashboard/               # Dashboard KPIs and summaries
├── scheduling/              # Shifts, templates, swap requests, tasks, timesheets
│   ├── views_agent.py        # Agent: list staff/shifts, create shift, optimize, etc.
│   └── urls.py               # /api/scheduling/...
│
├── staff/                    # Staff management (profiles, requests)
├── timeclock/                # Time clock / punch
├── reporting/                # Reports (sales, attendance, inventory)
├── notifications/            # In-app + push, WhatsApp webhook, announcements
│   ├── routing.py            # WebSocket: /ws/notifications/
│   ├── views_agent.py        # Agent: send WhatsApp
│   └── urls.py               # /api/notifications/...
│
├── pos/                      # Point of Sale (tables, orders, payments)
│   ├── webhooks.py           # Square, Toast, Clover webhooks
│   ├── views_agent.py        # Agent: sync menu/orders, sales summary, status
│   └── urls.py               # /api/pos/...
│
├── menu/                     # Menu and categories
├── inventory/                # Inventory (items, suppliers, orders, adjustments)
├── billing/                  # Billing and subscriptions (Stripe)
├── checklists/               # Checklist management
├── chat/                     # Chat (structure present)
├── core/                     # Shared utilities, permissions, middleware
├── cleaning/                 # Cleaning module
├── kitchen/                  # Kitchen (consumers/routing present)
├── analytics/                # Analytics (placeholder)
│
├── templates/                # Email templates (e.g. password reset, staff invite)
├── locale/                   # Translations
├── scripts/                  # Backup, migration, test scripts
├── docs/                     # Extra documentation (e.g. notifications)
│
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml        # web (Daphne), redis, celery_worker, celery_beat
├── .env.example              # Local/dev env template
└── .env.production.template  # Production env template (EC2/Docker)
```

### App summary

| App | Purpose |
|-----|---------|
| **accounts** | Auth (JWT, PIN login), registration, restaurant CRUD, staff invitations, agent context & accept-invitation |
| **attendance** | Attendance tracking and reporting |
| **dashboard** | Dashboard data and KPIs |
| **scheduling** | Schedules, templates, assigned shifts, swap requests, tasks, timesheets, agent scheduling APIs |
| **staff** | Staff profiles and requests |
| **timeclock** | Time clock / punch |
| **reporting** | Sales, attendance, inventory reports |
| **notifications** | Notifications, preferences, device tokens, announcements, WhatsApp webhook, WebSocket `/ws/notifications/` |
| **pos** | POS: tables, orders, payments, Square/Toast/Clover webhooks, agent sync and status |
| **menu** | Menu and categories |
| **inventory** | Inventory items, suppliers, purchase orders, adjustments |
| **billing** | Billing and subscriptions (Stripe) |
| **checklists** | Checklist management |
| **core** | Shared helpers, permissions, middleware |

---

## Prerequisites

- **Python** 3.13 (or version aligned with `Dockerfile`/CI)
- **PostgreSQL** (local or RDS)
- **Redis** (for Celery and Channels)
- **Node/npm** not required for backend-only runs

---

## Environment Variables

Copy the right template and fill in values.

- **Local / dev:** copy `.env.example` to `.env`
- **Production (Docker on EC2):** copy `.env.production.template` to `.env.production`

### Main variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret (use a strong random value in production) |
| `DEBUG` | `False` in production |
| `ALLOWED_HOSTS` | Comma-separated (e.g. `api.heymizan.ai`, `app.heymizan.ai`) |
| `POSTGRES_*` / `DB_*` | Database connection (see `settings.py`: prefers `POSTGRES_*`, falls back to `DB_*`) |
| `REDIS_HOST`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis and Celery (in Docker use `redis` as host) |
| `FIREBASE_SERVICE_ACCOUNT_KEY` | JSON string for Firebase Admin (push notifications) |
| `WHATSAPP_*` | WhatsApp Cloud API (webhook, verify token, etc.) |
| `LUA_*` | Lua agent (API key, agent ID, webhooks) |
| `STRIPE_*` | Stripe billing |
| `SQUARE_*` | Square POS (OAuth, webhooks) |
| `EMAIL_*` | SMTP for transactional email |
| `OPENAI_API_KEY` | Optional; for AI features |

See `.env.example` and `.env.production.template` for full lists and comments.

---

## Local Development

1. **Create and activate a virtualenv**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # or .venv\Scripts\activate on Windows
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**

   ```bash
   cp .env.example .env
   # Edit .env: set DB_* (or POSTGRES_*), SECRET_KEY, etc.
   ```

4. **Database**

   ```bash
   python manage.py migrate
   # Optional: python manage.py createsuperuser
   ```

5. **Run the server**

   ```bash
   python manage.py runserver
   # API: http://localhost:8000/api/
   # Admin: http://localhost:8000/admin/
   ```

6. **Celery (optional, for tasks and beat)**

   Start Redis, then:

   ```bash
   celery -A mizan worker --loglevel=info
   celery -A mizan beat --loglevel=info
   ```

7. **WebSockets**

   For real-time notifications, run Daphne instead of `runserver`:

   ```bash
   daphne -b 0.0.0.0 -p 8000 mizan.asgi:application
   ```

   WebSocket URL: `ws://localhost:8000/ws/notifications/`

---

## Docker

- **Dockerfile:** Python 3.13-slim image; installs system deps and `requirements.txt`; default `CMD` waits for `db` and runs migrate + Daphne (for setups that use a `db` service).
- **docker-compose.yml** defines:
  - **redis** — Redis 7, port 6379, healthcheck
  - **web** — Django app: `collectstatic`, `migrate`, then `daphne -b 0.0.0.0 -p 8000`; uses `.env.production`; port 8000
  - **celery_worker** — Celery worker
  - **celery_beat** — Celery beat

Database is expected **outside** the compose stack (e.g. RDS); set `POSTGRES_HOST` (or `DB_HOST`) in `.env.production` to your RDS endpoint.

**Run locally with Docker:**

```bash
# Ensure .env.production exists and POSTGRES_* point to a running DB
docker-compose up -d --build
# API: http://localhost:8000/api/
```

---

## Production Deployment

Typical flow on the server (e.g. EC2) where the app and Docker are already set up:

```bash
git pull
docker-compose down
docker-compose up -d --build
```

- **First time:** Copy `.env.production.template` to `.env.production`, fill in all values (DB, Redis, Stripe, Square, WhatsApp, Lua, etc.). Ensure `ALLOWED_HOSTS` includes your API domain (e.g. `api.heymizan.ai`).

- **After `up -d --build`:** The `web` container runs `collectstatic`, `migrate`, then starts Daphne. This can take **1–2 minutes**. If the frontend shows “Network error. Please check backend server.”, wait a bit and retry.

**Health check (on server):**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/
# or a known endpoint, e.g. /api/notifications/whatsapp-webhook/?...
```

**Logs:**

```bash
docker compose logs web --tail 100
docker compose logs celery_worker --tail 50
```

**CORS:** Backend allows `https://app.heymizan.ai` (and localhost for dev). If you use another frontend origin, add it to `CORS_ALLOWED_ORIGINS` in `mizan/settings.py`.

---

## API Overview

All API routes are under `/api/` (and optionally behind a reverse proxy at `https://api.heymizan.ai`).

| Prefix | App | Description |
|--------|-----|-------------|
| `/api/` | accounts | Auth (login, PIN login, JWT refresh), register, restaurant, staff, invitations, agent (context, accept-invitation, lookup) |
| `/api/dashboard/` | dashboard | Dashboard KPIs and summaries |
| `/api/attendance/` | attendance | Attendance endpoints |
| `/api/menu/` | menu | Menu and categories |
| `/api/inventory/` | inventory | Inventory, suppliers, orders, adjustments |
| `/api/reporting/` | reporting | Reports |
| `/api/timeclock/` | timeclock | Time clock |
| `/api/scheduling/` | scheduling | Schedules, shifts, swaps, tasks, timesheets, agent scheduling APIs |
| `/api/staff/` | staff | Staff management |
| `/api/notifications/` | notifications | Notifications, preferences, device tokens, announcements, WhatsApp webhook, health |
| `/api/pos/` | pos | Tables, orders, payments, webhooks (Square, Toast, Clover), agent sync/status |
| `/api/billing/` | billing | Billing and subscriptions |
| `/api/schema/` | drf-spectacular | OpenAPI schema |
| `/api/swagger-ui/` | drf-spectacular | Swagger UI |

**WebSocket**

- `ws://<host>/ws/notifications/` — real-time notifications (Django Channels).

**Admin**

- `/admin/` — Django admin (staff/superuser only).

---

## Integrations

- **Firebase** — Push notifications (FCM); requires `FIREBASE_SERVICE_ACCOUNT_KEY`.
- **WhatsApp Cloud API** — Webhook at `/api/notifications/whatsapp-webhook/`; invite and notification flows; configure `WHATSAPP_*` in env.
- **Lua Agent** — Context, accept-invitation, lookup, scheduling, POS sync, WhatsApp send; configure `LUA_*` and webhook URLs.
- **Stripe** — Billing and subscriptions; set `STRIPE_*` and webhook endpoints as needed.
- **Square** — POS OAuth and webhooks; set `SQUARE_*` and ensure `SQUARE_WEBHOOK_NOTIFICATION_URL` matches your Square app config.

---

## Scripts & Commands

**Management commands (examples):**

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

App-specific commands (see each app’s `management/commands/`):

- **accounts:** `cleanup_invites`, `create_initial_data`, `populate_languages`
- **notifications:** `check_notification_health`, `cleanup_notifications`, `send_scheduled_notifications`
- **core:** `db_consistency_check`, `reset_sequences`, `seed_data`

**Scripts in `scripts/`:**

- `backup_postgres.sh` — PostgreSQL backup
- `migrate_sqlite_to_postgres.sh` — Migration from SQLite to Postgres
- `create_task_templates.sql` — Task template seeding
- `test_checklist_flow.py`, `test_staff_request.py` — Manual/test scripts

---

## License & Contact

Part of the Mizan AI. For questions or access, contact the Mizan team.
