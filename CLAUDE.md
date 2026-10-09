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
- WebSocket: ws://localhost:8000/ws/appointments/ and ws://localhost:8000/ws/chats/ (session auth; clinic comes from the user)
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
| `billing` | `Subscription` (planes de la clínica) y `PatientInvoice`: factura de paciente que agrupa `PerformedProcedure`. Borrador editable; al emitir copia sus líneas (`lines`), congela `total`, toma número de la serie de su clínica (`InvoiceSequence`) y no vuelve a mirar los procedimientos. No se corrige: se anula (`void()`) y se emite otra |
| `agent` | WhatsApp bot state: `AgentMemory` (contexto del LLM), `ConversationSession` (hilo), `ChatMessage` (historial append-only, auditado), `ChatAttachment` (foto/nota de voz del paciente, irremplazable), `WorkflowError`, `AgentProfile` (personalidad del agente por clínica) |
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
  the same rules. So does the patient's profile photo (`Patient.photo`,
  `patients/photos.py`, prefix `patient-photos/`): cropped to a 512 px square
  JPEG without metadata, served only via `patients:photo` (AccessLog; private
  browser cache of 240 s, below the 300 s signature, because avatars repaint in
  live lists — URLs carry `?v=<updated_at>`), rendered by
  `patients/_avatar.html` (directory, patient file, chat list and thread),
  excluded from the API — but, unlike clinical photos, replaceable and removable
  (the old object is deleted on commit).
- **Serving any clinical file goes through `signed_url_for(document, user)`**,
  which checks permission and signs in the same function — there is no
  sign-without-checking path. It works for anything exposing `.file` and
  `.patient` (lesion photos, consent signatures). `GET
  /clinico/adjuntos/<public_id>/` and `GET
  /clinico/consentimientos/<public_id>/firma/` share one base view
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
- **Global procedure list (`clinical:procedure-list`).** All procedures of the
  clinic with filters (period — current month by default —, patient, professional,
  service, billed/unbilled) and totals. It reads clinical data, so it logs
  `AccessLog` (list, or search with `q`), unlike the appointment list. It shows
  and sums `frozen_*`, never the catalogue. The same screen and the dashboard
  warn of **completed appointments with a patient file and no procedure** in the
  last 90 days (`appointments.filters.completed_without_procedure`): work done
  that was never recorded, hence never billed.
- **Off-limits to the n8n token.** This layer has **no REST API** on purpose, so
  the agent's `Api-Key` cannot reach clinical data. `Visit` links *to*
  `Appointment`, never the reverse. The only HTTP surface is the session-only
  attachment view above. **Any new endpoint over this layer must instrument
  `AccessLog` and stay denied to the agent.**
- **Retention is not fixed in code.** `CLINICAL_RETENTION_YEARS` is a setting with
  a conservative default; there is no automatic purge (pending autonomic law).

### WhatsApp attachments (`agent.ChatAttachment`)

A photo a patient sends (a cut, a nail, a wound) is health data. It follows the
clinical-photo rules, plus a few of its own — see `agent/files.py` and
`agent/media.py`:

- **Same private bucket** (`clinical_media`), own prefix `chat-media/`, UUID key,
  object key stored, never a URL.
- **Validated by content and rewritten without metadata.** Images go through
  `validate_clinical_image(external=True)` and are then re-encoded from pixels
  (EXIF/GPS, XMP, PNG text all dropped; EXIF orientation applied first; ICC,
  transparency and animated frames kept). Audio: allow-list Ogg (Opus/Vorbis)
  and MP3 by byte signature. Size and checksum describe the *stored* file.
- **Irreplaceable.** One attachment per message (`OneToOne`), `save()` of an
  existing row and `delete()` raise, and a PostgreSQL trigger blocks any
  `UPDATE` (`agent/migrations/0009`). `DELETE` only happens through the cascade
  of a deleted conversation; the bucket object is kept.
- **Intake:** `POST /api/agent/messages/<id>/media/` (multipart `file`), agent
  `Api-Key` only, inbound image/audio messages only, 201/400/409. n8n downloads
  the binary from Meta in the `WA-Media-Ingest` sub-workflow, which **saves no
  executions** so the photo never stays in n8n.
- **Serving:** `GET /chats/adjuntos/<message_id>/` — staff session of the thread's
  **clinic** (not the patient: people without a file yet must be visible),
  agent denied, `AccessLog` per view, redirect to a signed URL that lives
  `CHAT_MEDIA_URL_EXPIRE` seconds (300) and makes the bucket answer
  `Cache-Control: no-store`. The redirect carries `no-store` and
  `Referrer-Policy: no-referrer`.
- **Never shown by surprise.** The thread renders a blurred placeholder with no
  image data ("Toca para ver"); the photo is only requested when opened in the
  viewer, so each `AccessLog` is a real view. Audio uses `preload="none"`.

**Outbound (staff → patient).** The composer can attach an image (JPEG/PNG ≤ 5 MB,
caption allowed) or an audio file (MP3/Ogg ≤ 16 MB, no text). `agent.services.send_staff_media()`
goes straight to the Cloud API like staff text (no n8n): message + `ChatAttachment` are created in one
transaction (an invalid file leaves nothing), the **stored** bytes are uploaded to Meta
(`whatsapp.upload_media` → `send_media`), and the message ends `sent`/`failed`. `attach_media()`
(the agent's intake) still refuses outbound messages. The agent is **not** told about these: nothing
touches `AgentMemory` or n8n. Delivery receipts arrive through the usual `statuses` forward.

`LesionAttachment.Source.PATIENT_WHATSAPP` already exists: promoting a chat photo
to a lesion is a planned follow-up (same bucket, keep the checksum).

### Multi-tenancy

All domain models reference `clinic_id`. Staff queries are automatically filtered by `user.clinic`. Superusers see all clinics. This isolation is enforced in DRF viewset `get_queryset()` methods.

### Custom user model

`core.User` extends `AbstractUser` with email as the login field (`username` is set equal to email). Users have a `clinic` FK and a `role` field (`ADMIN`/`STAFF`). Set `AUTH_USER_MODEL = 'core.User'` is already configured.

### URLs

Two conventions, on purpose:

- **Web panel (session, HTML) → Spanish.** Only exceptions: `/login/` and
  `/logout/`. Always link with `{% url %}` / `reverse()` by `name` (names stay in
  English), never with literal paths.
- **API, admin, WebSockets, healthz → English**, untouched: `/api/…`
  (router, `@action`s, `api/public/appointments/<token>/<action>/`),
  `/admin/`, `/ws/…`, `/healthz/`. They are the contract with n8n and the
  links already sent to patients; renaming them breaks both.

Panel map (namespace in brackets):

| Prefix | Routes |
|---|---|
| `/` (`core`) | `buscar/`, `login/`, `logout/`, `cuenta/` (`perfil/`, `horario/`, `contrasena/`), `clinica/` (`editar/`, `integraciones/` → redirect to `/agente/`), `panel/citas/<uuid>/` (`gestionar/`, `accion/`, `resumen/`) |
| `/citas/` (`appointments`) | `crear/`, `listado/`, `<uuid>/procedimiento/`, `mi-perfil/` (redirect) |
| `/profesionales/` (`professionals`) | `crear/`, `<pk>/editar/` — top-level, views live in `appointments` (`appointments/professional_urls.py`) |
| `/pacientes/` (`patients`) | `crear/`, `<id>/` + tabs `anamnesis/`, `alertas/`, `lesiones/`, `consentimientos/`, `procedimientos/`, `editar/`, `foto/` |
| `/servicios/` (`services`) | `crear/`, `<pk>/editar/`, `<pk>/eliminar/` |
| `/conocimiento/` (`knowledge`) | `crear/`, `<uuid>/editar/`, `<uuid>/eliminar/` |
| `/chats/` (`agent`) | `agente/`, `lista/`, `adjuntos/<uuid>/`, `<uuid>/` (`mensajes/`, `enviar/`, `escribiendo/`, `modo/`) |
| `/agente/` (`agent_settings`, admins only) | test chat at the root («Chat»), `probar/enviar/`, `personalidad/`, `configuracion/` (Meta credentials + webhook, two forms told apart by a hidden `form` field) — views in `agent/settings_views.py` |
| `/facturacion/` (`billing`) | `nueva/`, `pendientes/`, `<pk>/` (`emitir/`, `anular/`, `cobrar/`, `procedimientos/`, `eliminar/`) |
| `/clinico/` (`clinical`) | `procedimientos/` (listado global), `adjuntos/<uuid>/`, `consentimientos/<uuid>/firma/` |

### Agent personality (`agent.AgentProfile`)

One per clinic (`AgentProfile.for_clinic()` returns an unsaved default when
missing, so a clinic without one keeps the old tone). It only changes *style*:
name, tone, tú/usted, emojis (all coded choices), a welcome text for new
contacts and capped free `style_notes`. The prompt block is written in Django
(`agent/persona.py`, pure) and served ready-made at `GET /api/agent/profile/`
(clinic `Api-Key` only; the clinic comes from the key). n8n
(`WA-Inbound-Orchestrator con buffer`) calls it on every execution — both the
WhatsApp and the panel-test path — in the `Cargar Perfil Agente` node, and pastes
`persona_prompt` at the top of the system message; on failure it falls back to
the default tone. The fixed rules come *after* the block and it says it cannot
override them. Free text is flattened to one quoted line, so it cannot open a
new prompt section. Audited via `audit.registry`.

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
| Accent | `accent-fg` (text/icons), `accent-soft`, `accent-line` |
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

`Django Channels 4.1` + `channels-redis` + `Daphne` ASGI server. Both consumers (`AppointmentConsumer`, `agent.consumers.ChatConsumer`) extend `core.consumers.ClinicScopedConsumer`: the clinic is taken from `scope['user']`, **never from the URL**, and the group name comes from `core.realtime.clinic_group_name()` (hashed — `clinic_id` is free text). Consumers are read-only; writes stay as HTTP POSTs. Appointment changes broadcast via a `post_save` signal in `appointments/signals.py`. Chat events are emitted from the services in `agent/realtime.py` (not signals), with `transaction.on_commit`, and carry no message content — the browser fetches the rendered fragment. Every event carries the clinic's `total_unread`.

Browser side: `static/js/chat_live.js` (loaded by `base.html` for users with a clinic) keeps **one** socket per page, reconnects with exponential backoff (1 s → 30 s), treats close codes 4401/4403 as final, falls back to polling every 15 s after 3 failures, and emits `resync` to subscribers whenever events may have been missed. It keeps the sidebar unread badge (`[data-chat-unread]`) and the tab title up to date on every page. `static/js/chat_inbox.js` (Alpine `chatInbox`) fetches `GET /chats/<id>/mensajes/?after=<last id in DOM>` and `GET /chats/lista/`; dedup is by `data-message-id`. Structural changes (agent mode, clinic switch, 24 h window reopening) reload the page — never while a message is being typed.

Delivery receipts (✓ / ✓✓ / blue ✓✓ / failed): n8n forwards Meta's `statuses` to `POST /api/agent/messages/status/` → `agent.services.apply_delivery_status()`, which only moves a status **forward** (Meta does not guarantee order), fills `delivered_at`/`seen_at` once (`read_at` is the *clinic* reading an inbound message, not the patient), saves with `save()` (audited) and emits `chat_message`; the client re-renders that bubble with `?only=<id>`. The mobile menu button carries the same unread badge as the sidebar.

Human mode is enforced in n8n: on the real WhatsApp path the orchestrator calls
`GET /api/agent/sessions/should-reply/` (phone normalized like `record_message`)
after registering the message and ingesting media; if the agent must not reply
it stops there (the message stays in the panel). If it must, it first sends
Meta's typing indicator («escribiendo…», node `Mostrar Escribiendo`). While staff
types in the composer, `POST /chats/<id>/escribiendo/` →
`agent.services.signal_staff_typing()` does the same from Django (throttled to one
call per thread every 20 s, silent on failure). Both mark the patient's last
message as **read** in WhatsApp — Meta offers no typing without read.

### Background tasks (Celery)

Configured in `config/celery.py`. Beat schedule runs:
- `dispatch_24h_reminders` — every hour, for appointments in 24h
- `dispatch_2h_reminders` — every 15 minutes, for appointments in 2h

Broker: Redis DB 1. Result backend: Redis DB 2. Channel layer: Redis DB 0.

### Antelación mínima de reserva

`Clinic.min_booking_notice_minutes` (default 120, 0 = sin mínimo). Solo rige la vía
online (`require_online_booking=True`: agente y API); el staff del panel y el admin
quedan exentos. Se aplica en tres sitios de `appointments/services.py`: el motor de
huecos (`_generate_slots(not_before=…)`, tanto el del profesional como el de la
clínica), `create_appointment()` y `validate_appointment_update()` (solo si la hora
realmente se mueve, así que confirmar/cancelar una cita cercana no se bloquea). El
error es `BookingTooSoon` (`booking_too_soon`, 400).

### Citas sin ficha de paciente

Una cita puede existir antes que el paciente (primera visita desde el panel, o
reserva del agente sin onboarding). `Appointment.patient` queda vacío y la cita
lleva su contacto: `patient_name`, `patient_phone` (E.164) y `contact_email`.
Con ficha esos campos no mandan, se lee de `patient`.

- **Alta:** el panel (`AppointmentForm`, conmutador «Primera visita (sin ficha)»)
  y la API (`patient_name` + `patient_phone` obligatorios si no hay `patient`)
  pasan por `create_appointment()`, que normaliza el teléfono y, si la clínica ya
  tiene un paciente con ese número, enlaza la cita solo (`_resolve_contact`).
- **La ficha nace después:** `create_patient_from_appointment()` (acción
  `create_patient` en `core:dashboard-appointment-action`) crea o vincula sin
  duplicar, y `create_patient()` enlaza las citas huérfanas de ese teléfono
  (`link_orphan_appointments`, una a una con `save()`, solo de su clínica).
- **Barrera clínica:** sin ficha no se puede completar la cita ni registrar
  procedimientos (historia, consentimientos y facturación cuelgan del paciente).
  La capa clínica no se toca.
- **Agente:** el onboarding previo deja de ser obligatorio; `WA-Appointments-Manager`
  manda `first_name`/`last_name`/`email` como contacto en `action=create`.

### Contactos responsables (`patients.Guardian`)

Quien gestiona las citas de otros con su propio teléfono (el hijo que pide por sus
padres). `Guardian` es único por `(clinic, phone)` y se vincula a pacientes con
`PatientGuardian` (+ relación). `Guardian.patient` enlaza su propia ficha si
también es paciente. **Solo logística de citas: nunca ve datos clínicos.**
`Guardian.bookable_patients()` es la regla de a quién puede reservar: sus
vinculados y él mismo (si es paciente), sin archivados y sin nadie más.
`Patient.phone` es opcional (quien se gestiona por un contacto); el hilo de
WhatsApp resuelve primero ficha por teléfono y, si no hay, contacto
(`ConversationSession.guardian`). Un paciente archivado no admite citas nuevas
(`PatientArchived` en `create_appointment`).

**Agente.** `GET /api/agent/sessions/booking-context/?phone=` (solo `Api-Key`)
dice si el número es de un contacto con pacientes a su cargo (`ask_for_whom`) y
cuáles son (`candidates`, solo los suyos). El orquestador de n8n lo pide en el
nodo `Cargar Contexto Reserva` y, si hay que preguntar, el agente pregunta «¿para
quién?» y manda `patient_id` en los params de `tool_clinica`. Django lo hace
cumplir: `GET /api/patients/?phone=…&patient_id=…` solo devuelve la ficha si ese
número puede gestionarla, y `POST /api/appointments/` del agente exige
`requester_phone` y rechaza (400) pacientes que ese número no pueda reservar. El
`phone` de la tool lo fija n8n con el del remitente real, no el LLM.

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
backend). **Required in production**: without these, lesion photos, consent
signatures and WhatsApp attachments (`ChatAttachment`) all fail to upload:
```
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
```
Keep the bucket **private** (no public access, no custom public domain). Optional
overrides: `CLINICAL_MEDIA_URL_EXPIRE` (signed-URL seconds, default 600),
`CLINICAL_ATTACHMENT_MAX_BYTES`, `CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL`,
`CHAT_MEDIA_URL_EXPIRE` (WhatsApp attachments, default 300),
`CHAT_AUDIO_MAX_BYTES` (default 16 MB).

### Deploy (GitHub Actions → GHCR → Coolify)

La imagen **se construye fuera del servidor** (un build en el host provocaba picos
de RAM que podían tumbar n8n, que comparte máquina). Coolify solo hace `pull` y
reinicia. Flujo:

1. Push a la rama `prod` (se llega con `git merge main` en `prod`; `main` no
   despliega nada).
2. `.github/workflows/deploy.yml` construye dos imágenes con caché de capas y las
   sube a GHCR con los tags `:prod` y `:<sha>`: `ghcr.io/jesuslj0/auto-clinic`
   (Django; la usan `web`, `celery` y `celery-beat`) y
   `ghcr.io/jesuslj0/auto-clinic-nginx`.
3. El último paso llama a la API de Coolify
   (`/api/v1/deploy?uuid=…`). Coolify relee `docker-compose.prod.yml` de `prod`,
   hace `pull` (`pull_policy: always`) y recrea los contenedores.

Reglas:

- **`docker-compose.prod.yml` no tiene `build:`**: solo `image:`. Si se añade, el
  servidor volvería a construir. `docker-compose.yml` es el de desarrollo y sí
  construye.
- **El recurso de Coolify sigue siendo Docker Compose** (db, redis, nginx con
  labels de Traefik, volúmenes), no «Docker Image». Con **auto-deploy y webhook de
  git desactivados**: el workflow es el único camino de despliegue. Si se
  reactivan, cada push haría además un build en el servidor.
- **Migraciones**: van en el `command` de `web`; `celery` y `celery-beat` no las
  lanzan.
- **Variables**: los secretos reales viven en Coolify (runtime). El build no
  necesita `.env`: `collectstatic` usa el `SECRET_KEY` por defecto de `base.py`.
- **Secrets de GitHub** (*Settings → Secrets → Actions*): `COOLIFY_TOKEN` (API
  token de Coolify), `COOLIFY_URL` (sin barra final) y `COOLIFY_APP_UUID` (último
  tramo de la URL del recurso). `GITHUB_TOKEN` lo pone Actions solo.
- **Pull privado**: el servidor necesita `docker login ghcr.io` hecho como `root`
  en el host (terminal de *Servers → localhost*, no la del contenedor `coolify`)
  con un PAT classic de solo `read:packages`. **El PAT caduca**: cuando lo haga,
  Actions seguirá en verde pero el `pull` fallará con `unauthorized`. Renovar y
  repetir el `docker login`.
- **Rollback**: cada imagen lleva su `sha`. Cambiar `:prod` por `:<sha>` en los
  cuatro servicios de `docker-compose.prod.yml`, push a `prod` y redesplegar.
- Los tags `:<sha>` se acumulan en GHCR; limpiarlos de vez en cuando
  (*Packages → Package settings*). En el servidor, *Force Docker Cleanup*
  de Coolify retira las capas antiguas.

Fallos típicos del último paso del workflow: `401` = token, `404` = UUID, error de
conexión = `COOLIFY_URL`. `manifest unknown` en Coolify = intentó bajar la imagen
antes de que existiera; relanzar el deploy.

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
- Static files served by WhiteNoise in production, with **hashed names**
  (`CompressedManifestStaticFilesStorage`, set in `base.py` because the
  Dockerfile's `collectstatic` runs with the default settings — the manifest
  must be built there). Always reference static files with `{% static %}`,
  never a literal `/static/…` path. Tests use plain `StaticFilesStorage`.
