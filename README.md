# Auto Clinic

Base de Django lista para producción: un SaaS de gestión de citas para clínicas.

## Stack
- Django 5 con settings separados (`base`, `dev`, `prod`)
- Persistencia sobre PostgreSQL
- APIs con Django REST Framework para todas las apps de dominio
- Django Channels + Redis como channel layer, para las citas en tiempo real
- Celery + Redis (broker y backend) para los recordatorios
- Docker Compose para levantarlo en local

## Apps
- `core`: clínicas y usuarios personalizados
- `patients`: fichas de pacientes
- `services`: catálogo de servicios de la clínica
- `appointments`: reservas, profesionales y acciones públicas por token
- `notifications`: recordatorios y tareas de Celery
- `billing`: suscripción de la clínica (opcional)
- `booking`: reserva pública (solo plantillas)
- `portal`: portal del paciente para confirmar o cancelar por token (solo plantillas)
- `agent`: estado del bot de WhatsApp (AgentMemory, ConversationSession, WorkflowError)
- `knowledge`: base de conocimiento de la clínica (entradas, consultas, caché)

## Convenciones de código

### Comentarios: los justos

Un comentario solo cuando el código no se explique solo, y para decir **qué** hace
—lo que se ve de un vistazo—, nunca **por qué** se implementó así. Fuera las
justificaciones, las alternativas descartadas, las advertencias a futuros
lectores y la historia del cambio: eso va al mensaje del commit, al PR o al
README de la app, no al código.

- **Python**: docstrings de una o dos líneas en clases y funciones cuyo nombre no
  baste. Comentarios sueltos solo para marcar una sección o aclarar una línea
  genuinamente opaca.
- **Plantillas Django**: prácticamente ninguno. La única excepción es un rótulo
  corto de sección o de componente cuando ayuda a orientarse en un fichero largo:

  ```django
  {# Filtros #}
  {# Tabla de resultados #}
  ```

  Nada de bloques `{% comment %}` explicando decisiones de diseño, mecánicas de
  htmx o Alpine, ni por qué un atributo está donde está.
- **JavaScript y CSS**: igual que Python. Una línea sobre qué hace una función si
  el nombre no lo dice; el resto, fuera.

Si una decisión necesita explicación para no deshacerse por error, documéntala en
el README de su app (`clinical/README.md`, `audit/README.md`…), que es donde se
busca ese tipo de cosas.

## Endpoints principales de la API

| Recurso | URL base |
|---------|----------|
| Servicios | `/api/services/` |
| Profesionales | `/api/professionals/` |
| Citas | `/api/appointments/` |
| Pacientes | `/api/patients/` |

### Flujo de reserva por profesional (agente)
1. `GET /api/services/` — servicios disponibles
2. `GET /api/professionals/?service={id}` — profesionales que prestan ese servicio
3. `GET /api/professionals/{id}/available-slots/?date=YYYY-MM-DD` — huecos libres
4. `POST /api/appointments/` — crear la cita

La referencia completa está en `ENDPOINTS.md`.

## Ciclo de vida de una cita

Estados: `pending` → `confirmed` → `completed` (o `no_show`); `cancelled` puede
ocurrir desde cualquier estado vivo.

- Una cita `pending` ya bloquea el hueco (lo retiene). Las reservas del agente y
  las públicas nacen `pending`; las que crea el personal nacen `confirmed`.
- Confirmar una cita («ponerla en firme») es cosa de la clínica: solo el personal,
  desde el panel de gestión o con `POST /api/appointments/{id}/confirm/`. Que el
  paciente responda «SÍ» a un recordatorio solo registra su confirmación
  (`patient_confirmed_at`); **no** cambia el estado.
- Una cita solo se puede marcar **completada** cuando está `confirmed`. El panel
  oculta el botón «Marcar completada» hasta entonces, así que el paso de
  confirmación no se puede saltar.
- Las retenciones sin confirmar caducan a los `Clinic.hold_ttl_minutes` y las
  cancela la tarea `expire_appointment_holds` (Celery beat), que libera el hueco.

## Puesta en marcha
1. Copia `.env.example` a `.env`.
2. Ejecuta `docker compose up --build`.
3. Abre `http://localhost:8000/admin/` o `http://localhost:8000/api/`.
4. Conecta un cliente WebSocket a `ws://localhost:8000/ws/appointments/<clinic_id>/`.

## Tareas de recordatorio
- `dispatch_24h_reminders`
- `dispatch_2h_reminders`
- `send_appointment_reminder`

## Endpoint público de acciones sobre citas
- `POST /api/public/appointments/<uuid:token>/confirm/`
- `POST /api/public/appointments/<uuid:token>/cancel/`
