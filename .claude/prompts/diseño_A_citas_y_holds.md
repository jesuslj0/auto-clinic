# Tarea: Diseño A — pending bloquea, holds con caducidad, y separación de la semántica de confirmación

## Repositorio
clinic-app (Django 5.x + DRF). NO tocar clinic-workflows.

## Contexto — el problema que se cierra

Hoy `BLOCKING_STATUSES = {CONFIRMED}` y `confirmed` lo escribe **el paciente**
respondiendo "SÍ" al recordatorio de 24h (vía `/api/public/appointments/{token}/confirm/`).

Consecuencia: el hueco de las 9:00 del día 20 está **libre para reservar hasta el
día 19**. Cualquier número de pacientes puede reservarlo, a todos se les responde
*"Tu cita ha sido registrada para el 20 a las 9:00"*, y el día 19 se lo queda el
primero que conteste al recordatorio. Al resto se les cancela una cita que
pidieron hace semanas y que se les confirmó por escrito.

Además `confirmed` significa hoy dos cosas incompatibles a la vez: "el staff la
validó" y "el paciente reconfirmó asistencia". Son ejes ortogonales metidos en un
campo, y por eso los flags `reminder_*` están haciendo de estado.

**Diseño elegido (A):**
- `pending` **bloquea** el hueco. Una reserva del agente cierra el slot al instante.
- Las citas nacidas del agente llevan `hold_expires_at`. Si el staff no las valida
  a tiempo, un cron las cancela y **libera el hueco explícitamente**.
- La confirmación del paciente pasa a ser un **timestamp**, no un estado.
- `confirmed` pasa a significar **solo**: "la clínica la tiene en firme".

---

## Preámbulo obligatorio — inspección antes de escribir código

Verifica todo, no asumas. Resúmeme antes de escribir una línea.

1. `BLOCKING_STATUSES` en `models.py` (~L317) y su reexport en `services.py`.
   Enumera **todos** los sitios que la leen.
2. `find_overlap()` y su firma.
3. `create_appointment()`, `validate_appointment_update()`, `ineligibility_reason()`,
   `select_professional_for_appointment()`.
4. La función que sirve `available-slots` (clínica y profesional).
5. `AppointmentActionByTokenAPIView` — qué hace exactamente hoy `confirm` y `cancel`.
   ¿Pasan por un service o escriben directo?
6. La query de `pending-reminders` — cómo filtra hoy (`status`, `reminder_24h_sent`,
   `reminder_3h_sent`, `reminder_responded`).
7. ¿Existe algún endpoint autenticado para que el **staff** confirme una cita?
   (`POST /api/appointments/{id}/confirm/`). Creo que no. Confírmalo.
8. ¿Cómo confirma hoy el staff una cita desde el panel? ¿Hay vista/acción, o solo
   el admin?
9. Los management commands existentes y cómo se ejecutan (cron, celery, otro).
10. El mecanismo de errores de dominio (`APIException` con `{code, message, details}`).

**Para y espera mi OK.**

---

## FASE 1 — Separar la semántica de confirmación (NO tocar `BLOCKING_STATUSES` todavía)

### 1.1 Campos nuevos en `Appointment`

```python
class Source(models.TextChoices):
    AGENT   = 'agent',   'Agente WhatsApp'
    STAFF   = 'staff',   'Panel'
    BOOKING = 'booking', 'Reserva pública'

source = models.CharField(max_length=20, choices=Source.choices,
                          default=Source.STAFF, db_index=True)
patient_confirmed_at = models.DateTimeField(null=True, blank=True)  # UTC
hold_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)  # UTC
```

En `Clinic`:
```python
hold_ttl_minutes = models.PositiveIntegerField(default=1440)  # 24h; 0 = sin caducidad
```

### 1.2 La confirmación del paciente deja de tocar `status`

**Regla, y coméntala en el modelo porque alguien la va a querer "arreglar":**

- Paciente responde **"SÍ"** → `patient_confirmed_at = now()`. **`status` NO cambia.**
  Es un hecho registrado, no una transición: el hueco ya estaba bloqueado antes y
  sigue bloqueado después. No libera ni ocupa nada.
- Paciente responde **"NO"** → `status = cancelled`. **Esto sí es transición**,
  porque libera un recurso.

La asimetría es intencionada.

Implementación:
- `services.py`: separa en **dos** funciones con guardias de transición explícitas:
  - `register_patient_confirmation(appointment)` → escribe `patient_confirmed_at`.
  - `confirm_by_clinic(appointment, user=None)` → `pending → confirmed`, y
    **pone `hold_expires_at = None`**.
- `AppointmentActionByTokenAPIView`, rama `confirm` → llama a
  `register_patient_confirmation()`. **Mantiene su URL y su contrato de respuesta
  exactos.** n8n no se entera.
- Rama `cancel` → sigue cancelando (por su service, nunca `.update()`).
- **Nunca `.update()` para cambios de estado. Nunca.**

### 1.3 Endpoint de confirmación del staff (falta)

`POST /api/appointments/{id}/confirm/` — autenticado, `IsStaffOrAdmin` → llama a
`confirm_by_clinic()`. Serializer + view + url + test.

Y expón la acción en el panel (vista o acción de lista) para que el staff pueda
validar sin entrar al admin. Si ya existe algo, reutilízalo.

### 1.4 `pending-reminders` deja de depender de los flags como estado

El filtro pasa a leerse solo:

```python
Appointment.objects.filter(
    status__in=BLOCKING_STATUSES,        # la cita sigue viva
    patient_confirmed_at__isnull=True,   # el paciente aún no ha dicho nada
    reminder_24h_sent=False,
    scheduled_at__range=(...),
)
```
(y el de 3h: `reminder_24h_sent=True, reminder_3h_sent=False`).

**El contrato de respuesta de `pending-reminders` NO cambia.** Cambia por dentro.

### 1.5 `source` se fija en la capa que llama

- Serializer / API → `Source.AGENT` (o `BOOKING` si viene del flujo público).
- ModelForm del panel / admin → `Source.STAFF`.
- Igual que `require_online_booking`: **parámetro explícito, sin default silencioso**
  en el service. `services.py` no toca `request`.

### 1.6 `hold_expires_at` se fija al crear

En `create_appointment()`:
- `source=AGENT` (o `BOOKING`) y `clinic.hold_ttl_minutes > 0`
  → `hold_expires_at = now() + timedelta(minutes=clinic.hold_ttl_minutes)`
- `source=STAFF` → `hold_expires_at = None`. Las citas del staff **nacen
  `confirmed`**, no `pending`.

### 1.7 Data migration

- `patient_confirmed_at = updated_at` donde `status='confirmed' AND reminder_responded=True`
  (esas confirmaciones vinieron del paciente).
- `status` se deja como está.
- `source`: las existentes, lo que puedas inferir; si no, `STAFF` por defecto.
- `hold_expires_at = None` para todas las existentes (no caduques citas ya creadas).

### 1.8 Tests fase 1

- "SÍ" del paciente → `patient_confirmed_at` puesto, `status` **intacto**.
- "NO" del paciente → `cancelled`.
- Staff confirma → `pending → confirmed`, `hold_expires_at` a `None`.
- Cita creada por staff → nace `confirmed`, sin hold.
- Cita creada por API → nace `pending`, con `hold_expires_at` correcto.
- `clinic.hold_ttl_minutes = 0` → sin hold.
- `pending-reminders` incluye citas `confirmed` sin `patient_confirmed_at`
  (**el bug que esto arregla**: hoy una cita validada por el staff se queda sin
  recordatorio).
- Contrato de `/api/public/appointments/{token}/confirm/` idéntico al de antes.

### ⛔ PARADA OBLIGATORIA

Al terminar la fase 1: **ejecuta toda la suite y párate.** Dime qué pasa, qué
falla, y espera mi OK antes de empezar la fase 2. No las mezcles: si algo se pone
rojo, quiero saber cuál de las dos fases lo rompió.

---

## FASE 2 — `pending` bloquea (tras mi OK)

### 2.1 El cambio

```python
BLOCKING_STATUSES = frozenset({Status.PENDING, Status.CONFIRMED})
```

**Una línea.** Todo lo demás (motor de slots, `find_overlap()`,
`ineligibility_reason()`, `create_appointment()`, `validate_appointment_update()`)
la lee y se ajusta solo. **Si tienes que tocar algo más para que funcione, es que
hay una duplicación escondida: encuéntrala y dímela, no la parchees.**

Ejecuta la suite. Los tests `test_pending_appointment_does_not_block` y similares
se pondrán rojos: **reescríbelos invirtiendo la expectativa** (`pending` ahora
bloquea). No los borres.

### 2.2 Revalidación transaccional (anti-carrera)

Entre que `available-slots` responde y llega el `POST /appointments/` pasan varios
mensajes de WhatsApp. Dos conversaciones pueden colarse por el mismo hueco.

En `create_appointment()`:
- Todo dentro de `transaction.atomic()`.
- `select_for_update()` sobre las citas bloqueantes del profesional en el rango.
- **Revalidar disponibilidad justo antes de crear** (no confiar en la del slot).
- Si el hueco ya no está → error de dominio `slot_unavailable`, formato estándar,
  `message` sin IDs ni trazas, apto para que n8n lo muestre o lo reformule.
- Test de concurrencia real (dos escrituras compitiendo): **solo una gana**.

Aplica lo mismo a `validate_appointment_update()` si permite mover a un hueco ocupado.

### 2.3 Caducidad de holds

Management command `expire_appointment_holds`:
- Busca `status=PENDING`, `hold_expires_at__lt=now()`, `hold_expires_at__isnull=False`.
- Las pasa a `cancelled` **a través del service**, con motivo distinguible
  (`expired`), nunca `.update()`.
- Idempotente. Loguea cuántas expiró.
- Documenta cómo programarlo (según lo que uses hoy: cron, celery beat…).

Si existe un campo de motivo de cancelación, úsalo. Si no, **no crees uno nuevo en
esta tanda** — dímelo y lo decidimos.

### 2.4 Tests fase 2

- Cita `pending` bloquea el hueco en `available-slots` **y** en la creación
  (mismo comportamiento en ambos: si el motor no lo ofrece, el POST tampoco lo acepta).
- Dos creaciones concurrentes en el mismo hueco → una 201, otra `slot_unavailable`.
- Hold caducado → el command lo cancela y **el hueco vuelve a aparecer** en
  `available-slots`.
- Hold **no** caducado → sigue bloqueando.
- Cita `confirmed` (staff la validó) → **nunca** caduca, aunque tuviera hold.
- Cita `source=staff` → nunca caduca.
- Command idempotente: dos ejecuciones seguidas, la segunda no hace nada.

---

## Fuera de scope — NO tocar

- **Nada de clinic-workflows / n8n.** Ningún JSON de workflow.
- **Contratos de respuesta**: `POST /api/appointments/`, `available-slots`,
  `pending-reminders`, `/api/public/appointments/{token}/confirm/` y `/cancel/`.
  Ni un campo más ni uno menos. Esto es innegociable: n8n consume todo eso hoy.
- No añadas el estado `requested` ni una bandeja de solicitudes. Ese es el diseño B,
  **descartado**.
- No cambies los mensajes al paciente (viven en n8n).
- Los modelos `Patient`, `Service`, `Professional`, `Reminder`.
- Los 16 tests preexistentes que ya fallaban.

---

## Verificación final

- [ ] Ninguna cita viva sin profesional (invariante anterior intacta).
- [ ] `BLOCKING_STATUSES` sigue definida **en un solo sitio** y todo la lee.
- [ ] La fase 2 requirió cambiar **solo** esa constante en código de producción
      (si no, reporta la duplicación que encontraste).
- [ ] `status` nunca se cambia con `.update()`. Todas las transiciones pasan por
      services con guardia.
- [ ] `patient_confirmed_at` nunca modifica `status`.
- [ ] `services.py` no conoce `request` ni HTTP.
- [ ] Todos los contratos de API listados arriba, idénticos. Hay tests que lo prueban.
- [ ] `test_n8n_payload_without_professional_creates_appointment` en verde.
- [ ] Los 450 tests siguen pasando; los 16 preexistentes fallan igual (ni uno más).

Al terminar: resumen por fase, migraciones creadas, y **si la fase 2 necesitó más
que cambiar la constante — y por qué**.