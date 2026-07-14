# Tarea: revertir `professional` obligatorio y añadir auto-asignación en services.py

## Repositorio
clinic-app (Django 5.x + DRF). NO tocar clinic-workflows.

## Contexto — por qué (léelo, no es decorativo)

En el último cambio se hizo `professional` requerido en `AppointmentSerializer`.
Eso ha roto la creación de citas desde el agente de WhatsApp: el workflow
`WA-Appointments-Manager` (nodo "B - Crear Cita Django") envía este body y **no
incluye `professional`**:

```json
{"clinic": …, "patient": …, "service": …, "scheduled_at": …, "end_at": …, "status": "pending"}
```

Cada creación desde el agente devuelve ahora 400. **El agente está caído en
producción.**

Y no se arregla añadiendo el campo en n8n: el endpoint de disponibilidad a nivel
de clínica (`/api/appointments/available-slots/`) devuelve slots sin decir qué
profesional los cubre, así que n8n tendría que **elegir el profesional él mismo**
— es decir, implementar lógica de asignación en un Code node. Eso viola el
principio del proyecto: la lógica de negocio vive en `services.py`, n8n solo
orquesta conversación.

**Objetivo:** mantener la invariante correcta (ninguna cita se crea sin
profesional) resolviéndola en Django, sin cambiar el contrato de la API y sin
tocar n8n.

## Preámbulo obligatorio — inspección antes de escribir código

No asumas rutas, nombres de funciones ni campos: verifícalo todo y resúmemelo
antes de escribir una línea.

Localiza y lee:
1. `AppointmentSerializer` — el cambio exacto que hizo `professional` requerido
   (¿`required=True`? ¿un `validate_professional`? ¿`extra_kwargs`?).
2. El `ModelForm` del panel de citas y el cambio del admin (la eliminación de la
   opción "— Sin asignar —").
3. `appointments/services.py` — la función de creación de cita, la de
   disponibilidad, y `BLOCKING_STATUSES` (línea ~17).
4. `appointments/models.py` — `find_overlap()` (línea ~248) y su firma actual.
5. El modelo `Professional`: confirma qué campos existen **hoy**
   (`is_active`, `accepts_online_booking`, `buffer_minutes`,
   `slot_granularity_minutes`, M2M a `Service`, `ProfessionalSchedule`,
   `ProfessionalTimeOff`). **Es probable que algunos NO existan todavía** — la
   tanda de hardening de `Professional` puede no estar aplicada. Dime exactamente
   cuáles hay y cuáles no.
6. La migración `0012_alter_appointment_professional.py` — qué hace exactamente.
7. Los tests que se tocaron: `test_create_requires_professional` y los que se
   actualizaron para pasar `professional` por API.

Cuando termines, dime en ~10 líneas: qué encontraste, qué campos de `Professional`
existen realmente, y cómo vas a implementar la selección. **Espera mi OK.**

---

## Cambios a implementar

### 1. Revertir la obligatoriedad de `professional` en la capa de entrada

- **`AppointmentSerializer`**: `professional` vuelve a ser **opcional**
  (`required=False`). Si viene, se valida. Si no viene, se auto-asigna.
- **Admin**: revertir a opcional.
- **ModelForm del panel**: aquí sí puede seguir siendo **obligatorio** (el staff
  sabe a quién asigna, y el desplegable ya filtra por `is_active=True`). Mantén
  ese cambio, pero **no lo impongas a nivel de serializer**.
- **NO toques la migración `0012`.** `null=True` en BD es correcto y necesario
  para el `on_delete=SET_NULL`. La invariante se aplica en la capa de creación,
  no en el schema.
- Elimina o reescribe `test_create_requires_professional`: ya no refleja el
  comportamiento deseado.

### 2. Auto-asignación en `services.py`

Nueva función pública en `appointments/services.py`:

```python
def select_professional_for_appointment(*, clinic, service, scheduled_at, end_at):
    """
    Devuelve el Professional que debe atender la cita, o lanza un error de
    dominio si no hay ninguno disponible.
    """
```

Criterios de elegibilidad, en este orden (aplica solo los que los campos
existentes permitan — si un campo no existe todavía, **omite ese criterio y
dímelo**, NO lo añadas como parte de esta tarea):

1. `professional.clinic == clinic`
2. El profesional presta ese `service` (M2M `services`)
3. `is_active=True` (si el campo existe)
4. `accepts_online_booking=True` (si el campo existe)
5. Tiene horario (`ProfessionalSchedule`) que cubre el tramo `[scheduled_at, end_at]`
   (si el modelo existe y el motor de slots ya lo usa)
6. No tiene ausencia (`ProfessionalTimeOff`) solapando (si el modelo existe)
7. **No tiene una cita solapada según la regla de bloqueo vigente** — reutiliza
   `BLOCKING_STATUSES` y `find_overlap()`. **NO hardcodees estados aquí.** Si
   mañana cambia la regla de bloqueo, esta función debe seguirla automáticamente.

**Criterio de desempate cuando hay varios elegibles:** el que tenga **menos citas
bloqueantes ese día** (reparto de carga simple). Si hay empate, el de menor `id`
(determinista, para que los tests sean estables). Documenta la regla en el
docstring.

**Si no hay ninguno elegible**, lanza un error de dominio que la view traduzca al
formato estándar del proyecto:

```json
{"code": "no_professional_available", "message": "…", "details": {…}}
```

Usa el mecanismo de errores que ya exista en el proyecto (búscalo — no inventes
uno nuevo). El `message` debe ser apto para que n8n lo muestre tal cual o lo
reformule: sin IDs internos, sin trazas.

### 3. Validación cuando `professional` SÍ viene en el payload

Si el cliente (panel, o n8n en el futuro) manda `professional` explícitamente, el
service debe validarlo con **los mismos criterios** que usa la auto-asignación —
no una lista paralela. Extrae la elegibilidad a un helper compartido, p.ej.:

```python
def _eligible_professionals_qs(*, clinic, service, scheduled_at, end_at): ...
```

y que tanto la validación explícita como la selección automática lo usen. Si el
profesional indicado no está en ese queryset → error de dominio con `code`
específico (p.ej. `professional_unavailable`). **Cero duplicación de reglas.**

### 4. La creación de cita nunca produce `professional=None`

El service de creación de cita debe garantizar que, tras ejecutarse, la cita
tiene profesional. Si no lo tiene (ni explícito ni auto-asignado), **no se crea**:
lanza el error de dominio. Que esto sea imposible de saltarse por accidente desde
cualquier vía (API, form, admin).

### 5. Tests

- Crear cita **sin** `professional` vía API → 201, y la cita tiene profesional.
- Crear cita sin `professional` cuando no hay ninguno elegible → 400 con
  `code: no_professional_available`.
- Crear cita **con** `professional` válido → 201, respeta el indicado.
- Crear cita con `professional` de otra clínica → 400.
- Crear cita con `professional` que no presta ese servicio → 400.
- Desempate: dos profesionales elegibles, uno con más citas ese día → elige el
  menos cargado. Empate → menor `id`.
- **Test de regresión con el payload literal de n8n** (`clinic`, `patient`,
  `service`, `scheduled_at`, `end_at`, `status: "pending"`, sin `professional`)
  → 201. Este test es el que impide que esto vuelva a pasar.
- Los tests que se actualizaron para pasar `professional` por API: revierte los
  que existían antes (deben volver a pasar sin `professional`), pero **deja
  también los que lo pasan explícitamente** — ambas vías deben funcionar.

---

## Fuera de scope — NO tocar

- **`BLOCKING_STATUSES`.** Déjalo exactamente como está (`{CONFIRMED}`). La
  decisión sobre si `pending` debe bloquear está pendiente y se aborda en otra
  tanda. Tu código debe **leer** la constante, nunca redefinirla ni asumir su
  contenido.
- **`find_overlap()`** — úsalo, no lo cambies.
- La confirmación por token (`AppointmentActionByTokenAPIView`) y los
  services/métodos de confirm/cancel. El agujero de doble confirmación es
  conocido y se cierra aparte.
- El contrato de respuesta de `available-slots` y de `POST /api/appointments/`.
- El modelo `Professional`: **no añadas campos nuevos.** Si `is_active` o
  `accepts_online_booking` no existen, omite ese criterio y avísame — NO los crees.
- Los modelos `Patient`, `Clinic`, `Service`, `Reminder`.
- Nada de clinic-workflows / n8n.

---

## Verificación antes de dar por terminada

- [ ] El payload literal de n8n (sin `professional`) crea la cita correctamente.
- [ ] Ninguna vía de creación puede producir una cita con `professional=None`.
- [ ] Toda la lógica de elegibilidad está en `services.py`, en **un** helper
      compartido. Ni serializer ni view calculan nada.
- [ ] `BLOCKING_STATUSES` se lee, no se duplica ni se hardcodea en ningún sitio nuevo.
- [ ] No hay import circular entre `models.py` y `services.py`. Si lo hay, dímelo
      **antes** de resolverlo por tu cuenta.
- [ ] El error `no_professional_available` sigue el formato estándar del proyecto
      y su `message` no expone IDs ni detalles técnicos.
- [ ] `makemigrations --check` limpio (esto no debería requerir migraciones nuevas).
- [ ] Tests nuevos pasan y los existentes siguen pasando.

Al terminar: resumen de archivos tocados y de qué criterios de elegibilidad
quedaron activos vs omitidos por campos inexistentes.