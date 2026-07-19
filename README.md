# Auto Clinic

Production-ready Django SaaS starter for a clinic appointment system.

## Stack
- Django 5 with split settings (`base`, `dev`, `prod`)
- PostgreSQL-ready persistence
- Django REST Framework APIs for all domain apps
- Django Channels + Redis channel layer for real-time appointment updates
- Celery + Redis broker/backend for reminders
- Docker Compose for local orchestration

## Apps
- `core`: clinics and custom users
- `patients`: patient records
- `services`: catalog of clinic services
- `appointments`: booking workflows, professionals, and public token actions
- `notifications`: reminders and Celery tasks
- `billing`: optional clinic subscription management
- `booking`: public booking flow (templates only)
- `portal`: patient portal for confirm/cancel via token (templates only)
- `agent`: WhatsApp bot state (AgentMemory, ConversationSession, WorkflowError)
- `knowledge`: clinic knowledge base (entries, queries, cache)

## Key API endpoints

| Resource | Base URL |
|----------|----------|
| Services | `/api/services/` |
| Professionals | `/api/professionals/` |
| Appointments | `/api/appointments/` |
| Patients | `/api/patients/` |

### Professional booking flow (agent)
1. `GET /api/services/` — list available services
2. `GET /api/professionals/?service={id}` — professionals offering that service
3. `GET /api/professionals/{id}/available-slots/?date=YYYY-MM-DD` — free slots
4. `POST /api/appointments/` — create the appointment

See `ENDPOINTS.md` for the full reference.

## Appointment lifecycle

States: `pending` → `confirmed` → `completed` (or `no_show`); `cancelled` can
happen from any live state.

- A `pending` appointment already blocks the slot (it holds it). Agent and public
  bookings are born `pending`; staff bookings are born `confirmed`.
- Confirming an appointment ("putting it in firm") is the clinic's job: staff
  only, via the management panel or `POST /api/appointments/{id}/confirm/`. A
  patient answering "YES" to a reminder only records attendance
  (`patient_confirmed_at`) — it does **not** change the status.
- An appointment can only be marked **completed** once it is `confirmed`. The
  management panel hides the "Marcar completada" button until then, so the
  confirmation step cannot be skipped.
- Unconfirmed holds expire after `Clinic.hold_ttl_minutes` and are cancelled by
  the `expire_appointment_holds` task (Celery beat), which frees the slot again.

## Quickstart
1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`.
3. Visit `http://localhost:8000/admin/` or `http://localhost:8000/api/`.
4. Connect a WebSocket client to `ws://localhost:8000/ws/appointments/<clinic_id>/`.

## Reminder Tasks
- `dispatch_24h_reminders`
- `dispatch_2h_reminders`
- `send_appointment_reminder`

## Public Appointment Action Endpoint
- `POST /api/public/appointments/<uuid:token>/confirm/`
- `POST /api/public/appointments/<uuid:token>/cancel/`
