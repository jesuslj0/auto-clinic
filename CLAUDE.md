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
| `agent` | WhatsApp bot state: `AgentMemory` (contexto del LLM), `ConversationSession` (hilo), `ChatMessage` (historial append-only), `WorkflowError` |
| `knowledge` | Clinic knowledge base: `ClinicKnowledgeBase`, `ClinicInfoQuery`, `ClinicInfoCache` |
| `audit` | Append-only audit trail: `ChangeLog` (writes, via signals) and `AccessLog` (reads, instrumented per view) |
| `clinical` | Clinical core: `MedicalHistory`, `Episode`, `Visit`, `ClinicalNote` (SOAP), `Addendum`. Immutable after signing; soft-delete only. Also versioned anamnesis: `QuestionnaireTemplate`, `TemplateVersion`, `Question`, `QuestionnaireResponse` (immutable literal snapshot) |
| `core.models.SoftDeleteModel` | Reusable soft-delete mixin (`deleted_at`, `objects`/`all_objects`, `can_be_deleted()`) |

### Auditing (mandatory for clinical data)

The project stores health data: special category under GDPR art. 9, part of the
medical record under Ley 41/2002. Two rules apply to every new piece of the
clinical layer:

- **Every clinical model must be registered in the audit trail**, from its
  app's `AppConfig.ready()`: `audit.registry.register(Model, sensitive=[...])`.
  Nothing is audited automatically. Clinical free text goes in `sensitive`, so
  the log records *that* a field changed but never its value.
- **Every view that exposes clinical data must instrument the read**, with
  `AccessLogMixin` (CBV), `AuditedViewSetMixin` (DRF) or `log_access()`.
  Reads emit no signals, so an uninstrumented view leaves no trace.

Bulk ORM operations (`bulk_create`, `queryset.update()`, `queryset.delete()`)
skip signals and are **not** audited — never use them on a registered model.
See `audit/README.md`.

### Clinical layer (`clinical`)

The clinical core (`MedicalHistory`, `Episode`, `Visit`, `ClinicalNote`,
`Addendum`) is the most legally-sensitive part of the project. Rules for anyone
touching it — see `clinical/README.md` for the full picture:

- **Immutable after signing.** A signed `ClinicalNote` accepts no content
  `UPDATE` and no `DELETE`, ever — enforced both in `save()`/`can_be_deleted()`
  and by a PostgreSQL trigger (`clinical/migrations/0002`). The only possible
  change is adding an `Addendum` (append-only). Never weaken this.
- **Anamnesis is versioned, and answers are frozen.** Publishing a
  `TemplateVersion` freezes it and its `Question`s; changing a questionnaire
  means publishing a new version, never editing the old one. A
  `QuestionnaireResponse` stores a literal `snapshot` (question text + answer),
  not FKs to `Question`, so later edits can never rewrite what a patient
  answered. Same two levels (`clinical/migrations/0004`).
- **Nothing is physically deleted.** Every model uses `SoftDeleteModel`; deletion
  is logical and cascades manually (`delete()` overrides). `Addendum` is
  append-only.
- **Off-limits to the n8n token.** This layer has **no REST API** on purpose, so
  the agent's `Api-Key` cannot reach clinical data. `Visit` links *to*
  `Appointment`, never the reverse. **Any new endpoint over this layer must
  instrument `AccessLog` and stay denied to the agent.**
- **Retention is not fixed in code.** `CLINICAL_RETENTION_YEARS` is a setting with
  a conservative default; there is no automatic purge (pending autonomic law).

### Multi-tenancy

All domain models reference `clinic_id`. Staff queries are automatically filtered by `user.clinic`. Superusers see all clinics. This isolation is enforced in DRF viewset `get_queryset()` methods.

### Custom user model

`core.User` extends `AbstractUser` with email as the login field (`username` is set equal to email). Users have a `clinic` FK and a `role` field (`ADMIN`/`STAFF`). Set `AUTH_USER_MODEL = 'core.User'` is already configured.

### REST API

DRF `ModelViewSet` + `DefaultRouter` at `/api/`. Custom permissions:
- `IsClinicAdminOrReadOnly` — write restricted to clinic admins
- `IsStaffOrAdmin` — staff and above

Filtering via `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`.

### Front-end theming (light/dark)

Tailwind runs from the Play CDN — there is no build step and no CSS file. The
whole design system lives in `templates/partials/_head_theme.html`, included by
the four root templates (`base.html`, `registration/login.html`, `404.html`,
`500.html`). **Never duplicate `tailwind.config` anywhere else.**

Dark mode is *not* done with a `dark:` variant on every element. It uses a
**semantic palette over CSS variables**: a card is `bg-surface`, not
`bg-white dark:bg-slate-800`. Switching theme reassigns the variables under
`.dark` and the markup is untouched.

When writing new markup, use the tokens, never `slate-*` / `white` directly:

| Purpose | Tokens |
|---|---|
| Backgrounds | `canvas` (page), `surface`, `surface-raised`, `muted`, `muted-strong` |
| Borders | `line`, `line-strong` |
| Text | `content`, `content-muted`, `content-subtle`, `content-faint` |
| Brand | `brand-fg` (text/icons), `brand-soft`, `brand-soft-strong`, `brand-line` |
| States | `danger`, `success`, `warning`, `info` — each with `-soft` and `-line` |

Exceptions that stay literal: `text-white` on brand-coloured buttons, the
`bg-slate-900/50` modal overlays, and the white backdrop behind clinic logos
(transparent PNGs would vanish in dark mode). Status classes rendered from
Python (`appointments/templatetags/appointment_extras.py`, form widgets in
`appointments/forms.py`) must use tokens too.

The theme is stored in `localStorage` under `ac-theme`, defaults to the OS
preference, and is toggled by `partials/_theme_toggle.html` (included once per
sidebar). Its state lives in `window.acTheme`, wired up with a delegated click
listener, so the partial can be included any number of times.

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
