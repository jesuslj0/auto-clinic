# Tarea: acotar `accepts_online_booking` a la vía online + auditar la elegibilidad en actualización de citas

## Repositorio
clinic-app (Django 5.x + DRF). NO tocar clinic-workflows.

## Contexto

En la tanda anterior se centralizó la elegibilidad de profesionales en
`ineligibility_reason()` (services.py) y se garantizó la invariante "ninguna cita
se crea sin profesional" en `create_appointment()`.

Quedaron dos cabos sueltos:

**(A) `accepts_online_booking` se está aplicando también al staff.** El campo
significa "el agente de WhatsApp / la reserva pública pueden ofrecer a este
profesional". Su caso de uso es el profesional que solo acepta citas por teléfono
o derivación interna. Aplicándolo también al panel, una clínica que active el flag
descubre que **su propio staff tampoco puede asignarle citas** — lo que convierte
el campo en un `is_active` redundante y lo deja sin utilidad.

**(B) La actualización de citas (`PATCH`) puede no pasar por el service.** Se quitaron
del serializer las validaciones de elegibilidad y se dejó `professional` opcional.
Hay que verificar si eso reabrió por la puerta de atrás el mismo bug que se
acaba de cerrar por delante.

## Preámbulo obligatorio — inspección antes de escribir código

No asumas nada. Verifica y resúmeme antes de escribir una línea.

### Sobre (A)
1. Firma actual de `ineligibility_reason()` y de la función de auto-selección.
2. **Todos** los llamantes de esas funciones. Para cada uno, dime si el origen es
   *online* (agente/API, reserva pública) o *staff* (ModelForm del panel, admin).
3. ¿Existe ya en `Appointment` algún campo tipo `source` / `origin` / `channel`?
   (Creo que no, pero compruébalo.)

### Sobre (B) — auditoría, no arreglo todavía
4. ¿Cómo se actualiza hoy una cita vía `PATCH /api/appointments/{id}/`?
   ¿Pasa por algún service, o el `ModelSerializer.update()` por defecto escribe
   directo?
5. ¿Existe un `update_appointment()` / `reschedule_appointment()` en `services.py`?
6. **Escribe primero tests que demuestren si estos dos agujeros existen** (no los
   arregles aún):
   - `PATCH` con `{"professional": null}` sobre una cita existente → ¿queda la
     cita sin profesional?
   - `PATCH` con un `scheduled_at` nuevo que cae en un `ProfessionalTimeOff` del
     profesional actual, o encima de una cita bloqueante → ¿se acepta?
   - `PATCH` con un `professional` nuevo que no presta el servicio, o de otra
     clínica → ¿se acepta?
   Ejecútalos y **dime cuáles pasan (agujero cerrado) y cuáles fallan (agujero
   abierto)**.
7. ¿Qué otras vías pueden modificar `professional` o `scheduled_at` de una cita
   existente? (vista de edición del panel, admin, bulk-update, drag&drop del
   calendario si existe…). Enuméralas.

**Para y espera mi OK con el resultado de la inspección. No arregles nada todavía.**

---

## Cambios a implementar (tras mi OK)

### 1. Acotar `accepts_online_booking` a la vía online

`ineligibility_reason()` (y la auto-selección) reciben un flag explícito:

```python
def ineligibility_reason(professional, *, clinic, service, scheduled_at, end_at,
                         require_online_booking: bool, exclude_pk=None): ...
```

- `require_online_booking=True` → llamadas desde el **serializer / API**, la reserva
  pública, y cualquier vía no autenticada por staff.
- `require_online_booking=False` → **ModelForm del panel** y **admin**.

**Todos los demás criterios se aplican igual en ambos casos**: clínica, servicio
que presta, `is_active`, `ProfessionalSchedule`, `ProfessionalTimeOff`,
solapamiento con citas bloqueantes. El staff tampoco debe poder meter una cita
encima de otra confirmada ni en las vacaciones de alguien. **Lo único que se
relaja es `accepts_online_booking`.**

**Punto crítico y sutil — no te lo saltes:** la distinción es **quién llama**, NO
**si `professional` venía en el payload**. Si mañana n8n manda `professional`
explícito, sigue siendo una reserva online y `accepts_online_booking` debe
aplicarse. Es decir:

| Origen | ¿`professional` explícito? | ¿Aplica `accepts_online_booking`? |
|---|---|---|
| API / agente | no (auto-asigna) | **Sí** |
| API / agente | sí | **Sí** |
| Panel / admin | sí | **No** |
| Panel / admin | no (auto-asigna) | **No** |

Es decir: el flag va atado al **llamante**, no al camino interno. Si acabas
poniéndolo solo en la rama de auto-asignación, está mal.

**No hagas que el flag dependa de `request.user`, ni pases el `request` a
`services.py`.** El service recibe un booleano explícito; quien decide su valor
es la capa que llama (serializer → `True`, form/admin → `False`). `services.py` no
sabe nada de HTTP.

Tests:
- Profesional con `accepts_online_booking=False`: auto-asignación vía API lo
  **descarta**; si es el único elegible → `no_professional_available`.
- Mismo profesional, **enviado explícitamente** vía API → rechazado
  (`professional_unavailable`).
- Mismo profesional, asignado desde el **ModelForm del panel** → **aceptado**.
- Mismo profesional, asignado desde el **admin** → **aceptado**.
- Ese mismo profesional con `is_active=False` → rechazado **también** desde el
  panel (para probar que solo se relaja `accepts_online_booking`, no el resto).

### 2. Cerrar los agujeros de la actualización (solo los que la inspección confirme)

Objetivo: **la elegibilidad se valida en actualización con las mismas reglas que en
creación, en el mismo helper, sin duplicar nada.** Y la invariante pasa a ser
"ninguna cita **existe** sin profesional", no solo "se crea".

- Si el `PATCH` puede dejar `professional=null` → impedirlo. Una cita viva siempre
  tiene profesional. (`null=True` sigue en BD por el `SET_NULL`; la regla se aplica
  en la capa de escritura.)
- Si el `PATCH` puede mover `scheduled_at` / `end_at` / `professional` sin
  revalidar → revalidar con `ineligibility_reason(..., exclude_pk=appointment.pk)`.
- Si no existe un service de actualización, créalo (`update_appointment()` y/o
  `reschedule_appointment()`), y que **el serializer y el form deleguen en él**.
  Serializers y views no calculan; orquestan.
- Aplica lo mismo a **todas** las vías que enumeraste en el punto 7 de la
  inspección — incluido `bulk-update` si permite tocar `professional` o
  `scheduled_at`.
- El `require_online_booking` de la actualización sigue la misma regla: API → `True`,
  panel/admin → `False`.

Los tests que escribiste en la inspección para demostrar los agujeros pasan a ser
los tests de regresión. Que queden en verde.

---

## Fuera de scope — NO tocar

- **`BLOCKING_STATUSES`.** Sigue siendo `{CONFIRMED}`. Léela, no la cambies ni la
  redefinas. La decisión sobre si `pending` debe bloquear es otra tanda.
- **No añadas `source`, `hold_expires_at`, `patient_confirmed_at` ni ningún estado
  nuevo.** Van en la tanda siguiente. El flag `require_online_booking` es un
  parámetro de función, **no** un campo del modelo.
- La confirmación por token (`AppointmentActionByTokenAPIView`) y los services de
  confirm/cancel.
- El contrato de respuesta de `POST /api/appointments/`, `available-slots` y
  `pending-reminders`.
- El modelo `Professional`: no añadas ni quites campos.
- Los modelos `Patient`, `Clinic`, `Service`, `Reminder`.
- Nada de clinic-workflows / n8n.
- Los 16 tests que ya fallaban antes (tests en inglés contra plantillas traducidas,
  permisos de `WorkflowError`). Déjalos como están, no entran aquí.

---

## Verificación antes de dar por terminada

- [ ] `accepts_online_booking` se aplica a **todas** las llamadas desde la API
      (con y sin `professional` explícito) y a **ninguna** desde panel/admin.
- [ ] `services.py` no recibe `request` ni consulta `request.user`. Solo un booleano.
- [ ] Todos los demás criterios de elegibilidad siguen aplicándose por igual en
      ambas vías. Hay un test que lo demuestra (`is_active=False` rechazado también
      desde el panel).
- [ ] Ninguna vía de escritura (API, PATCH, form, admin, bulk-update) puede dejar
      una cita viva con `professional=None`.
- [ ] Toda actualización que toque `professional` / `scheduled_at` / `end_at`
      revalida elegibilidad con `exclude_pk`, usando el **mismo** helper que la
      creación. Cero reglas duplicadas.
- [ ] `test_n8n_payload_without_professional_creates_appointment` sigue en verde.
- [ ] `BLOCKING_STATUSES` sigue valiendo `{CONFIRMED}` y no se redefine en ningún sitio.
- [ ] Los 450 tests que pasaban siguen pasando. Los 16 preexistentes siguen fallando
      igual (ni uno más).
- [ ] `makemigrations --check` limpio (esto no debería necesitar migraciones).

Al terminar: resumen de archivos tocados, y **cuáles de los agujeros del PATCH
resultaron estar realmente abiertos** vs ya cubiertos.