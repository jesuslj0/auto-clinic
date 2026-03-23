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
- `appointments`: booking workflows and public token actions
- `notifications`: reminders and Celery tasks
- `billing`: optional clinic subscription management

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
