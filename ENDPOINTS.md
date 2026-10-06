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
| `PatientViewSet` | `/api/patients/` | `IsStaffOrAdmin` | first_name, last_name, email, phone | clinic, phone | first_name, last_name, email, phone, created_at |
| `ServiceViewSet` | `/api/services/` | `IsStaffOrAdmin` | name, description | clinic, is_active | name, price, duration_minutes, created_at |
| `ProfessionalViewSet` | `/api/professionals/` | `IsStaffOrAdmin` | user__first_name, user__last_name, user__email | clinic, professional_type, service | user__first_name, user__last_name, user__email, professional_type |
| `AppointmentViewSet` | `/api/appointments/` | `IsStaffOrAdmin` | patient__first_name, patient__last_name, patient_name, patient_phone, status | clinic, status, service, patient, professional, patient_phone, reminder_24h_sent, reminder_3h_sent, reminder_responded, scheduled_at_gte, scheduled_at_lte | scheduled_at, status, created_at, patient_name |
| `ReminderViewSet` | `/api/reminders/` | `IsStaffOrAdmin` | — | clinic, appointment, reminder_type, success | scheduled_for, sent_at, reminder_type, success |

> `PatientViewSet` y `AppointmentViewSet` soportan **BulkCreate** y **BulkUpdate**.

#### Campos del serializer de Profesional

El recurso `/api/professionals/` devuelve:

| Campo | Tipo | Notas |
|-------|------|-------|
| `id` | int | Solo lectura |
| `user` | int (FK) | ID del usuario vinculado |
| `user_info` | objeto | `id, first_name, last_name, email, full_name` (solo lectura) |
| `clinic` | int (FK) | ID de la clínica |
| `professional_type` | string | `medico`, `dentista`, `psicologo`, `enfermero`, `fisioterapeuta`, `nutricionista`, `podologo` |
| `professional_type_display` | string | Etiqueta del choice, sin género (solo lectura) |
| `professional_type_label` | string | Etiqueta concordada con `title` («Podóloga» si es `dra`). Sin tratamiento cae en `professional_type_display` (solo lectura) |
| `title` | string | Tratamiento: `dr`, `dra`, `d`, `dna`, o vacío (solo lectura) |
| `title_display` | string | `Dr.`, `Dra.`, `D.`, `Dña.` (solo lectura) |
| `display_name` | string | Nombre con tratamiento: «Dra. Elena Garrido» (solo lectura) |
| `bio` | string | Presentación, máx. 500 caracteres (solo lectura) |
| `services_detail` | array | Servicios que ofrece: `id, name, duration_minutes, duration_display, price, price_display, is_active` (solo lectura) |
| `service_ids` | array | IDs de servicios para escritura |
| `schedules` | array | Horario semanal recurrente: `id, day_of_week, day_of_week_display, start_time, end_time, is_active`. Hora **local de la clínica**, no UTC. Un día puede traer varios tramos (jornada partida) (solo lectura) |
| `is_active` | bool | Profesional dado de alta |
| `accepts_online_booking` | bool | Admite reserva online |
| `buffer_minutes` | int | Margen entre citas |
| `slot_granularity_minutes` | int | Paso de la rejilla de huecos |

> El agente de WhatsApp (`action=list_professionals`) se apoya en `display_name`,
> `professional_type_label`, `bio` y `schedules`: es de donde saca el horario
> semanal cuando el paciente pregunta «¿qué horario tiene la podóloga?».
> `schedules` incluye los tramos desactivados, así que el consumidor debe
> filtrar por `is_active`.
>
> **`title` y `bio` son de solo lectura, y la colegiación (`license_number`,
> `license_body`) no se expone.** A este endpoint llega el token de n8n, que
> admite `POST` y `PATCH`: el agente necesita leer el tratamiento y la
> presentación para presentar al profesional, no para reescribirlos. La ficha se
> edita desde el panel (`ProfessionalForm`).

#### Campos adicionales del serializer de Cita (AppointmentSerializer)

| Campo | Tipo | Notas |
|-------|------|-------|
| `professional_name` | string | Nombre completo del profesional (solo lectura) |
| `professional_type` | string | Tipo del profesional (`medico`, etc.) (solo lectura) |
| `professional_type_display` | string | Etiqueta legible del tipo (solo lectura) |

#### Acciones personalizadas en ProfessionalViewSet

| Método | URL | Descripción | Parámetros |
|--------|-----|-------------|------------|
| GET | `/api/professionals/{id}/available-slots/` | Slots libres del profesional en una fecha | `date` (req.), `duration`, `start_hour`, `end_hour` |
| GET | `/api/professionals/{id}/services/` | Servicios que ofrece el profesional | — |

> **Antelación mínima:** los huecos que empiezan antes de `ahora + Clinic.min_booking_notice_minutes`
> (120 por defecto, 0 = sin mínimo) no se devuelven, y crear o reprogramar por la API una cita
> dentro de ese plazo responde 400 con `code: booking_too_soon`. El staff desde el panel no está sujeto.

**Ejemplo de respuesta `available-slots`** (`earliest_bookable` es el primer instante reservable por la vía online, en la zona de la clínica: `ahora + min_notice_minutes`; el agente lo usa para mencionar la antelación solo cuando es la causa de que una hora no se pueda reservar):
```json
{
  "professional_id": 1,
  "professional_name": "Dr. García",
  "date": "2026-04-20",
  "duration_minutes": 30,
  "min_notice_minutes": 120,
  "earliest_bookable": "2026-04-20T10:12:00+02:00",
  "available_slots": ["2026-04-20T08:00:00+02:00", "2026-04-20T08:30:00+02:00"]
}
```

#### Acciones personalizadas en AppointmentViewSet

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/api/appointments/{id}/status/` | Devuelve id, status, patient_name, scheduled_at, confirmation_token |
| GET | `/api/appointments/available-slots/` | Slots libres de la clínica (`date`, `duration`, `start_hour`, `end_hour`, `clinic`) |
| GET | `/api/appointments/pending-reminders/` | Citas pendientes de recordatorio (`type=24h\|3h`) |
| GET | `/api/appointments/export/` | Exportar citas |
| POST | `/api/appointments/bulk-create/` | Crear múltiples citas |
| PATCH | `/api/appointments/bulk-update/` | Actualizar múltiples citas |

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
| `AgentMemoryViewSet` | `/api/agent/memory/` | `IsStaffOrAdmin` \| `IsAgentClinicKey` | session_id | session_id | Memoria del LLM. Aislada por clínica |
| `WorkflowErrorViewSet` | `/api/agent/errors/` | `IsStaffOrAdmin` \| `IsAgentClinicKey` | workflow, workflow_name, node_name, phone, error_message | workflow, phone | Append-only (GET/POST). Aislado por clínica |
| `ConversationSessionViewSet` | `/api/agent/sessions/` | `IsStaffOrAdmin` \| `IsAgentClinicKey` | phone | clinic, phone | BulkCreate + BulkUpdate |
| `ChatMessageViewSet` | `/api/agent/messages/` | `IsStaffOrAdmin` \| `IsAgentClinicKey` | body | session, direction, sender, message_type | Append-only (GET/POST) + BulkCreate |

#### Personalidad del agente

| Método | URL | Permiso | Descripción |
|--------|-----|---------|-------------|
| GET | `/api/agent/profile/` | `IsAgentClinicKey` | Nombre, tono, trato, emojis, presentación y `style_notes` de la clínica de la clave, más `prompt`: el bloque ya redactado que n8n pega al principio del system message. Sin perfil guardado, devuelve los valores por defecto. |

#### Acción personalizada en ConversationSessionViewSet

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/api/agent/sessions/{id}/status/` | Devuelve id, phone, last_interaction, has_appointment_context, agent_paused, unread_count, updated_at |

#### Ingesta del historial de chat (`/api/agent/messages/`)

n8n publica cada mensaje —entrante del paciente y saliente del agente— con un POST.
La conversación se resuelve por `phone` (se crea si no existe) o se indica con `session`:

```json
{
  "phone": "+34600111222",
  "direction": "inbound",
  "sender": "patient",
  "body": "Hola, quiero pedir cita",
  "wa_message_id": "wamid.ABC123",
  "sent_at": "2026-07-21T10:30:00Z"
}
```

- `direction`: `inbound` | `outbound` · `sender`: `patient` | `agent` | `staff`
- **Idempotente por `wa_message_id`**: reenviar el mismo id devuelve 201 con el mensaje ya
  registrado, sin duplicarlo en el hilo ni volver a sumar no leídos.
- El teléfono se normaliza a E.164, así que `600 111 222` y `+34600111222` caen en el mismo hilo.
- Cada mensaje actualiza `last_message_at`, `last_message_preview` y `unread_count` de la sesión.
- La clínica se toma de la `Api-Key` del agente: un POST no puede escribir en el hilo de otra clínica.

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
| GET | `/citas/` | `AppointmentCalendarView` | Autenticado |
| GET | `/citas/listado/` | `AppointmentListView` | Autenticado |

### Profesionales — `appointments/professional_urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/profesionales/` | `ProfessionalListView` | Autenticado |
| GET/POST | `/profesionales/crear/` | `ProfessionalCreateView` | Autenticado |
| GET/POST | `/profesionales/{id}/editar/` | `ProfessionalUpdateView` | Autenticado |

### Pacientes — `patients/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/pacientes/` | `PatientListView` | Autenticado |
| GET | `/pacientes/<id>/` | `PatientDetailView` | Autenticado |

### Servicios — `services/urls.py`

| Método | URL | Vista | Acceso |
|--------|-----|-------|--------|
| GET | `/servicios/` | `ServiceListView` | Autenticado |
| GET/POST | `/servicios/crear/` | `ServiceCreateView` | Autenticado |

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
| `ws://host/ws/appointments/` | `AppointmentConsumer` | Sesión de Django (`AuthMiddlewareStack`) |
| `ws://host/ws/chats/` | `ChatConsumer` | Sesión de Django (`AuthMiddlewareStack`) |

La clínica **no va en la URL**: se toma de `scope['user'].clinic_id`, así que nadie puede suscribirse a otra clínica. Ambos heredan de `core.consumers.ClinicScopedConsumer`:

- Sin sesión (o usuario inactivo) → acepta y cierra con código `4401`.
- Usuario sin clínica (equipo de plataforma) → cierra con `4403`.
- Son de solo lectura: lo que mande el cliente se ignora.

El grupo se deriva con `core.realtime.clinic_group_name(stream, clinic_id)` (hash del id, porque `clinic_id` es texto libre). `AppointmentConsumer` retransmite `appointment_update`; `ChatConsumer` retransmite avisos sin contenido (`message`, `session`, `clinic`) emitidos desde `agent/realtime.py` tras el commit.

Al recibir un aviso de chats, el navegador pide el HTML a dos vistas de sesión (no API, sin acceso para el agente, 403 sin sesión):

| URL | Devuelve |
|-----|----------|
| `GET /chats/<session_id>/mensajes/?after=<message_id>` | Burbujas posteriores a ese mensaje, en orden. Sin `after`, las últimas 50. Máximo 200 por petición: `X-Has-More: 1` indica que hay que repetir con el último id. Un `after` de otro hilo → 400 (recargar el hilo). Marca el hilo como leído. |
| `GET /chats/lista/?q=&unread=&active=<session_id>` | La lista de conversaciones con los filtros de la bandeja. `X-Total-Unread` trae el total sin filtros. |
| `GET /chats/<session_id>/mensajes/?only=<message_id>` | Una sola burbuja, para repintarla cuando cambia (le llega la foto). |
| `GET /chats/adjuntos/<message_id>/` | Foto o audio del mensaje: solo staff de la clínica del hilo, deja `AccessLog` y redirige a una URL firmada de 5 min que el bucket sirve con `no-store`. |

Subida de adjuntos (solo n8n, `Api-Key` de clínica):

| Método | URL | Descripción |
|--------|-----|-------------|
| POST | `/api/agent/messages/<id>/media/` | Multipart `file`. Mensaje entrante de tipo imagen o audio. 201 guardado (sin URL en la respuesta) · 400 fichero rechazado por su contenido · 409 ya tenía adjunto (no se reemplaza) · 404 mensaje de otra clínica. |
| POST | `/api/agent/messages/status/` | Acuses de WhatsApp de los salientes (webhook `statuses` de Meta). Un objeto o una lista: `{wa_message_id, status: sent\|delivered\|read\|failed, timestamp?: segundos Unix o ISO, error?}`. El estado solo avanza (un acuse atrasado no lo devuelve atrás) y es idempotente. Un `wa_message_id` desconocido o de otra clínica responde 200 con `matched: false`. |

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
