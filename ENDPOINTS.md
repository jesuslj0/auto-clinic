# Endpoints — Auto Clinic

Referencia completa de todos los endpoints REST, vistas de plantilla y WebSockets del proyecto.

---

## 1. Autenticación

| Método | URL | Descripción | Acceso |
|--------|-----|-------------|--------|
| POST | `/api/auth/token/` | Obtener token DRF (username + password) | Público |

---

## 2. REST API — Endpoints por recurso

Base URL: `/api/`

Todos los ViewSets de DRF generan automáticamente las rutas estándar:
- `GET /api/<recurso>/` — Listar
- `POST /api/<recurso>/` — Crear
- `GET /api/<recurso>/{id}/` — Detalle
- `PUT /api/<recurso>/{id}/` — Actualizar completo
- `PATCH /api/<recurso>/{id}/` — Actualizar parcial
- `DELETE /api/<recurso>/{id}/` — Eliminar

### Core

| ViewSet | Ruta base | Permiso | Búsqueda | Filtros | Ordenación |
|---------|-----------|---------|----------|---------|------------|
| `ClinicViewSet` | `/api/clinics/` | `IsClinicAdminOrReadOnly` | name, clinic_id | — | name, clinic_id |
| `UserViewSet` | `/api/users/` | `IsClinicAdminOrReadOnly` | email, first_name, last_name | — | email, first_name, last_name, role |

> `UserViewSet` filtra por `user.clinic` automáticamente (salvo superusuarios).

### Datos clínicos

| ViewSet | Ruta base | Permiso | Búsqueda | Filtros | Ordenación |
|---------|-----------|---------|----------|---------|------------|
| `PatientViewSet` | `/api/patients/` | `IsStaffOrAdmin` | first_name, last_name, email, phone | clinic | first_name, last_name, email, phone, created_at |
| `ServiceViewSet` | `/api/services/` | `IsStaffOrAdmin` | name, description | clinic, is_active | name, price, duration_minutes, created_at |
| `AppointmentViewSet` | `/api/appointments/` | `IsStaffOrAdmin` | patient__first_name, patient__last_name, patient_name, patient_phone, status | clinic, status, service, patient | scheduled_at, status, created_at, patient_name |
| `ReminderViewSet` | `/api/reminders/` | `IsStaffOrAdmin` | — | clinic, appointment, reminder_type, success | scheduled_for, sent_at, reminder_type, success |

> `PatientViewSet` y `AppointmentViewSet` soportan **BulkCreate** y **BulkUpdate**.

#### Acción personalizada en AppointmentViewSet

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/api/appointments/{id}/status/` | Devuelve id, status, patient_name, scheduled_at, confirmation_token |

### Facturación

| ViewSet | Ruta base | Permiso | Filtros | Ordenación |
|---------|-----------|---------|---------|------------|
| `SubscriptionViewSet` | `/api/subscriptions/` | `IsClinicAdminOrReadOnly` | clinic, status, plan_name | plan_name, status, starts_at, ends_at, created_at |

### Knowledge Base

| ViewSet | Ruta base | Permiso | Búsqueda | Filtros | Notas |
|---------|-----------|---------|----------|---------|-------|
| `ClinicKnowledgeBaseViewSet` | `/api/knowledge/entries/` | `IsClinicAdminOrReadOnly` | title, content | clinic, kb_type, active | BulkCreate + BulkUpdate |
| `ClinicInfoQueryViewSet` | `/api/knowledge/queries/` | `IsStaffOrAdmin` | question, answer, intent_category | clinic, intent_category | BulkCreate |
| `ClinicInfoCacheViewSet` | `/api/knowledge/cache/` | `IsStaffOrAdmin` | normalized_question, answer | clinic, intent_category | — |

### Agente

| ViewSet | Ruta base | Permiso | Búsqueda | Filtros | Notas |
|---------|-----------|---------|----------|---------|-------|
| `AgentMemoryViewSet` | `/api/agent/memory/` | `IsStaffOrAdmin` | session_id | session_id | — |
| `WorkflowErrorViewSet` | `/api/agent/errors/` | `IsStaffOrAdmin` | workflow, workflow_name, node_name, phone, error_message | workflow, phone | Solo lectura (ReadOnlyModelViewSet) |
| `ConversationSessionViewSet` | `/api/agent/sessions/` | `IsStaffOrAdmin` | phone | clinic, phone | BulkCreate + BulkUpdate |

#### Acción personalizada en ConversationSessionViewSet

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/api/agent/sessions/{id}/status/` | Devuelve id, phone, last_interaction, has_appointment_context, updated_at |

---

## 3. Endpoints públicos (sin autenticación)

| Método | URL | Descripción |
|--------|-----|-------------|
| POST | `/api/public/appointments/<uuid:token>/confirm/` | Confirmar cita mediante token |
| POST | `/api/public/appointments/<uuid:token>/cancel/` | Cancelar cita mediante token |

---

## 4. Documentación API

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/api/schema/` | Schema OpenAPI (drf-spectacular) |
| GET | `/api/docs/` | Swagger UI interactivo |

---

## 5. Vistas de plantilla (HTML)

### Core — `core/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/` | `DashboardView` | Autenticado |
| GET/POST | `/login/` | `ClinicLoginView` | Público |
| POST | `/logout/` | `ClinicLogoutView` | Autenticado |

### Citas — `appointments/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/appointments/` | `AppointmentCalendarView` | Autenticado |
| GET | `/appointments/list/` | `AppointmentListView` | Autenticado |

### Pacientes — `patients/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/patients/` | `PatientListView` | Autenticado |
| GET | `/patients/<id>/` | `PatientDetailView` | Autenticado |

### Servicios — `services/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/services/` | `ServiceListView` | Autenticado |
| GET/POST | `/services/crear/` | `ServiceCreateView` | Autenticado |

### Reserva pública — `booking/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/booking/` | `BookingServiceListView` | Público |
| GET/POST | `/booking/datetime/` | `BookingDateTimeView` | Público |
| GET/POST | `/booking/confirm/` | `BookingConfirmView` | Público |
| GET | `/booking/success/` | `BookingSuccessView` | Público |

### Portal de pacientes — `portal/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/portal/<uuid:token>/` | `PortalAppointmentDetailView` | Público |
| GET/POST | `/portal/<uuid:token>/confirm/` | `PortalAppointmentConfirmView` | Público |
| GET/POST | `/portal/<uuid:token>/cancel/` | `PortalAppointmentCancelView` | Público |

---

## 6. WebSockets

| URL | Consumer | Autenticación |
|-----|----------|---------------|
| `ws://host/ws/appointments/<clinic_id>/` | `AppointmentConsumer` | `AuthMiddlewareStack` |

El consumer une al cliente al grupo `clinic_{clinic_id}_appointments` y retransmite eventos `appointment_update` a todos los clientes conectados de esa clínica.

---

## 7. Permisos

| Clase | GET | POST / PUT / DELETE |
|-------|-----|---------------------|
| `IsClinicAdminOrReadOnly` | Autenticado | Solo rol `admin` |
| `IsStaffOrAdmin` | Roles `admin` o `staff` | Roles `admin` o `staff` |
| Público (sin permiso) | Cualquiera | Cualquiera |

---

## 8. Panel de administración

| URL | Descripción |
|-----|-------------|
| `/admin/` | Django Admin (superusuarios) |
