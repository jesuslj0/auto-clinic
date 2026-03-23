# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Running the project

```bash
docker compose up --build    # Start all services (web, db, redis, celery)
docker compose up            # Start without rebuilding
docker compose down          # Stop all services
```

Services started:
- Web (Django/Daphne ASGI): http://localhost:8000
- Admin: http://localhost:8000/admin/
- REST API: http://localhost:8000/api/
- WebSocket: ws://localhost:8000/ws/appointments/<clinic_id>/
- PostgreSQL: port 5432
- Redis: port 6379
- Celery worker (background tasks)

### Django management

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver    # Uses config.settings.dev by default
```

### Settings modules

- `config.settings.dev` — development (DEBUG=True, console email backend)
- `config.settings.prod` — production (HTTPS enforcement, HSTS, secure cookies)

## Architecture

### Apps and responsibilities

| App | Purpose |
|-----|---------|
| `core` | Custom `User` model (email-based auth), `Clinic` model, `TimeStampedModel` base |
| `patients` | Patient records, scoped per clinic |
| `services` | Service catalog per clinic |
| `appointments` | Appointment lifecycle, WebSocket signals, token-based public actions |
| `notifications` | Celery beat tasks for reminder dispatch |
| `billing` | Subscription management |
| `booking` | Template-only public booking flow (no models) |
| `portal` | Template-only patient portal for confirm/cancel via token (no models) |

### Multi-tenancy

All domain models reference `clinic_id`. Staff queries are automatically filtered by `user.clinic`. Superusers see all clinics. This isolation is enforced in DRF viewset `get_queryset()` methods.

### Custom user model

`core.User` extends `AbstractUser` with email as the login field (`username` is set equal to email). Users have a `clinic` FK and a `role` field (`ADMIN`/`STAFF`). Set `AUTH_USER_MODEL = 'core.User'` is already configured.

### REST API

DRF `ModelViewSet` + `DefaultRouter` at `/api/`. Custom permissions:
- `IsClinicAdminOrReadOnly` — write restricted to clinic admins
- `IsStaffOrAdmin` — staff and above

Filtering via `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`.

### Real-time (WebSockets)

`Django Channels 4.1` + `channels-redis` + `Daphne` ASGI server. The `AppointmentConsumer` (`appointments/consumers.py`) is an `AsyncWebsocketConsumer` that joins a clinic-scoped group. Appointment changes broadcast via a `post_save` signal in `appointments/signals.py`.

### Background tasks (Celery)

Configured in `config/celery.py`. Beat schedule runs:
- `dispatch_24h_reminders` — every hour, for appointments in 24h
- `dispatch_2h_reminders` — every 15 minutes, for appointments in 2h

Broker: Redis DB 1. Result backend: Redis DB 2. Channel layer: Redis DB 0.

### Token-based public actions

`Appointment` has a UUID `confirmation_token` field. Patients can confirm or cancel without authentication:
- `POST /api/public/appointments/<uuid:token>/confirm/`
- `POST /api/public/appointments/<uuid:token>/cancel/`
- Template portal: `GET /portal/<token>/`

### Environment variables

Required in `.env`:
```
SECRET_KEY=
POSTGRES_DB=clinic
POSTGRES_USER=clinic
POSTGRES_PASSWORD=clinic
POSTGRES_HOST=db        # Docker service name
POSTGRES_PORT=5432
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
```

## Key notes

- **No tests exist** — the project has no test files or testing framework configured.
- **No linting configuration** — no flake8, black, or isort setup.
- Templates are in Spanish (recent migration from English).
- Static files served by WhiteNoise in production.
