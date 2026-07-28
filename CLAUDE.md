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
| `clinical` | Clinical core: `MedicalHistory`, `Episode`, `Visit`, `ClinicalNote` (SOAP), `Addendum`. Immutable after signing; soft-delete only. Also versioned anamnesis: `QuestionnaireTemplate`, `TemplateVersion`, `Question`, `QuestionnaireResponse` (immutable literal snapshot) , `ClinicalAlert` (per-patient, deactivated never deleted), `Lesion` (foot-map, coded zone + normalized coords) and its follow-up: `LesionObservation` (measurements per visit) + `LesionAttachment` (photo in a private R2 bucket, signed URLs only). `PerformedProcedure` links a visit to the service catalogue with the price frozen. Versioned informed consent: `ConsentTemplate`, `ConsentVersion`, `SignedConsent` (literal `text_copy` + signature in the private bucket) |
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
- **Informed consent is versioned, and the signed text is copied.** Same two
  levels as the anamnesis: publishing a `ConsentVersion` freezes it (here the
  text itself is frozen — the document *is* the text) and a draft cannot be
  signed. `SignedConsent.text_copy` stores the **full literal text** signed, not
  just the FK to the version, so republishing or restructuring can never rewrite
  what a patient agreed to. `SignedConsent.sign()` is the normal path; the record
  is immutable and never deleted (`clinical/migrations/0011`).
- **A `Lesion` stores its clinical zone and its drawing coordinates
  separately.** `anatomical_zone` is coded (never free text) and survives an SVG
  redesign; `x`/`y` are 0–1 fractions of the SVG, never pixels, and are only for
  rendering. Never derive one from the other. Location is frozen once created.
- **A lesion is a series, not a datum.** `LesionObservation` holds what was seen
  on each visit (measurements in mm as separate numeric fields — comparing them
  over time is the whole point) and `LesionAttachment` holds that day's photo.
  The visit must belong to the lesion's episode; `lesion`/`visit` are frozen.
  `lesion.evolution()` returns the series oldest-first, explicitly.
- **Clinical photos live in a private bucket, never in `MEDIA_URL`.** The
  `clinical_media` storage (Cloudflare R2, `default_acl=None`,
  `querystring_auth=True`) is separate from public media. Store the **object key
  only, never a URL** — signed URLs are generated per request and expire. Keys
  are UUIDs (`clinical/files.py`), never patient-derived names. Uploads are
  validated by **content** (size → byte signature → Pillow decode), allow-list
  JPEG/PNG/WebP, with a tighter size limit for non-professional sources; this
  happens in `save()`, so every intake path goes through it. An attachment is
  frozen once uploaded, and soft-deleting it keeps the bucket object. Consent
  signatures live in the same private bucket under their own key prefix, with
  the same rules.
- **Serving any clinical file goes through `signed_url_for(document, user)`**,
  which checks permission and signs in the same function — there is no
  sign-without-checking path. It works for anything exposing `.file` and
  `.patient` (lesion photos, consent signatures). `GET
  /clinical/attachments/<public_id>/` and `GET
  /clinical/consents/<public_id>/signature/` share one base view
  (`ProtectedFileRedirectView`), log an `AccessLog` `download_attachment` and
  redirect; the agent is denied explicitly.
- **A performed procedure freezes the catalogue, it does not read it.**
  `PerformedProcedure` copies `frozen_service_name` and `frozen_price` from the
  `Service` on its first `save()` and never re-reads the catalogue — a later
  price rise must not rewrite what last year's procedures cost. The FK to
  `Service` is provenance only (`DO_NOTHING`, `db_constraint=False`, so retiring
  a service neither cascades nor emits an unaudited bulk `UPDATE`). The frozen
  fields, the visit and the service are immutable afterwards; the treated zone is
  coded, never free text.
- **Alerts derive from the anamnesis by `Question.code`, never by text or
  order.** Rules live as data in `clinical/rules.py`; `evaluate_snapshot()` is
  pure (no DB) and `clinical/derivation.py` does the DB work, triggered by an
  explicit call in `QuestionnaireResponse.record()` — not a signal — so the
  panel, the patient form and the n8n path all go through it. The engine must
  never touch `source='manual'` alerts, and corrections deactivate, never
  delete.
- **Nothing is physically deleted.** Every model uses `SoftDeleteModel`; deletion
  is logical and cascades manually (`delete()` overrides). `Addendum` is
  append-only. A `ClinicalAlert` is never deleted either — `deactivate()` sets
  `is_active=False` and keeps the row, so "what was known back then" stays
  answerable.
- **Off-limits to the n8n token.** This layer has **no REST API** on purpose, so
  the agent's `Api-Key` cannot reach clinical data. `Visit` links *to*
  `Appointment`, never the reverse. The only HTTP surface is the session-only
  attachment view above. **Any new endpoint over this layer must instrument
  `AccessLog` and stay denied to the agent.**
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

Clinical photo storage (Cloudflare R2, private bucket — the `clinical_media`
backend). Without these, uploading a `LesionAttachment` fails; nothing else does:
```
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
```
Keep the bucket **private** (no public access, no custom public domain). Optional
overrides: `CLINICAL_MEDIA_URL_EXPIRE` (signed-URL seconds, default 600),
`CLINICAL_ATTACHMENT_MAX_BYTES`, `CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL`.

## Key notes

- **Tests: pytest + pytest-django**, configured in `pytest.ini`
  (`DJANGO_SETTINGS_MODULE = config.settings.test`). They live in `tests/`,
  mirroring the app layout (`tests/clinical/`, `tests/audit/`, …), with shared
  fixtures in `tests/conftest.py`. They need a reachable PostgreSQL — `.env`
  points `POSTGRES_HOST` at the Docker service, so from the host run:
  ```bash
  POSTGRES_HOST=localhost pytest              # whole suite
  POSTGRES_HOST=localhost pytest tests/clinical
  ```
- **No linting configuration** — no flake8, black, or isort setup.
- Templates are in Spanish (recent migration from English).
- Static files served by WhiteNoise in production.
