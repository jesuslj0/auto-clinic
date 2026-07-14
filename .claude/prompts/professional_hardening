# Tarea: Hardening del modelo Professional y del motor de disponibilidad

## Repositorio
clinic-app (Django 5.x + DRF). NO tocar clinic-workflows.

## Preámbulo obligatorio — inspección antes de escribir código

Antes de escribir una sola línea, inspecciona el estado actual del proyecto y
resume lo que encuentres. No asumas nada de lo que digo más abajo sobre nombres
de archivos, funciones o campos: verifícalo.

Localiza y lee:
1. El modelo `Professional` y `ProfessionalSchedule` (probablemente en la app de
   appointments o core — búscalo, no lo des por hecho).
2. El modelo `Clinic` (confirma el nombre exacto del campo de timezone) y el
   modelo `Service` (campo `duration_minutes`).
3. El `services.py` que contiene la lógica de generación de slots. Identifica la
   función exacta que hoy sirve a:
   - `GET /api/professionals/{id}/available-slots/`
   - `GET /api/appointments/available-slots/`
   Pega su firma actual y explica cómo calcula hoy los huecos: qué usa como hora
   de inicio/fin (¿los query params `start_hour`/`end_hour`? ¿`ProfessionalSchedule`?
   ¿una constante?), qué granularidad aplica, y si tiene en cuenta el horario del
   profesional o no.
4. `ProfessionalSerializer` y el FilterSet de `ProfessionalViewSet`.
5. Los tests existentes de disponibilidad, si los hay.
6. Si existe `ProfessionalCreateView` / `ProfessionalUpdateView` y su ModelForm.

Nota: el value de `ProfessionalType.PODOLOGO` ya ha sido corregido a mano
(`'podologo'`, sin tilde). Es posible que `makemigrations` genere una `AlterField`
por ese cambio de `choices`: **es esperado y correcto, déjala pasar**. No hay
datos en producción, así que no hace falta ninguna data migration para eso.

Cuando termines la inspección, dime en 10 líneas qué has encontrado y qué
cambios concretos vas a hacer. **Espera mi OK antes de escribir código.**

---

## Cambios a implementar (en este orden)

### 1. Campos nuevos en `Professional`
```python
is_active = models.BooleanField(default=True, db_index=True)
accepts_online_booking = models.BooleanField(default=True)
buffer_minutes = models.PositiveSmallIntegerField(default=0)
slot_granularity_minutes = models.PositiveSmallIntegerField(default=15)
```
- `is_active`: profesional dado de baja en la clínica. Debe excluirlo del motor
  de slots.
- `accepts_online_booking`: separa "trabaja aquí" de "el agente puede ofrecerlo".
- `buffer_minutes`: minutos muertos que se añaden **después** de cada cita al
  calcular ocupación.
- `slot_granularity_minutes`: paso del generador de slots. Si hoy hay una
  constante hardcodeada en `services.py` para esto, sustitúyela por este campo
  (y dímelo explícitamente).

### 2. Fix de `ProfessionalSchedule` — jornada partida
El `unique_together = [('professional', 'day_of_week')]` actual **impide la
jornada partida** (9:00–14:00 + 16:00–20:00), que es el caso normal en España.
Es un bug funcional, no un detalle de estilo.

- Sustituye `unique_together` por:
  `UniqueConstraint(fields=['professional', 'day_of_week', 'start_time'], name='uniq_prof_day_start')`
- Añade a `clean()` validación de solapamiento: ningún tramo activo del mismo
  profesional y mismo día puede solaparse con otro
  (`start_time__lt=self.end_time, end_time__gt=self.start_time`, excluyendo `self.pk`).
- Mantén la validación existente de `start_time >= end_time`.
- Añade un comentario en el modelo dejando claro que **`TimeField` aquí es hora
  LOCAL de la clínica**, no UTC — un horario recurrente no tiene UTC porque cambia
  con el horario de verano. La conversión a UTC ocurre en `services.py` al
  materializar los slots de una fecha concreta usando el timezone de la clínica.

### 3. Nuevo modelo `ProfessionalTimeOff`
Excepciones puntuales al horario recurrente (vacaciones, bajas, formación,
reuniones). Sin esto el agente de WhatsApp ofrece huecos que no existen.

```python
class ProfessionalTimeOff(models.Model):
    class Reason(models.TextChoices):
        VACATION = 'vacation', 'Vacaciones'
        SICK_LEAVE = 'sick_leave', 'Baja'
        TRAINING = 'training', 'Formación'
        OTHER = 'other', 'Otro'

    professional = models.ForeignKey(
        Professional, on_delete=models.CASCADE, related_name='time_off'
    )
    starts_at = models.DateTimeField()   # UTC
    ends_at = models.DateTimeField()     # UTC
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'professional_time_off'
        ordering = ['starts_at']
        indexes = [models.Index(fields=['professional', 'starts_at', 'ends_at'])]
```
`DateTimeField` (no `DateField`) a propósito: cubre tanto "toda la semana de
vacaciones" como "el martes de 11 a 13 tengo una reunión". `clean()` debe validar
`ends_at > starts_at`.

### 4. Actualizar el motor de slots en `services.py`
Toda la lógica nueva vive **exclusivamente aquí**. Las views solo orquestan; los
serializers no calculan nada.

La función de disponibilidad debe:
1. Devolver lista vacía si el profesional tiene `is_active=False`.
2. Construir los tramos del día a partir de **todos** los `ProfessionalSchedule`
   activos de ese `day_of_week` (varios tramos, no uno).
3. Convertir esas horas locales a UTC usando el timezone de la clínica, para la
   fecha concreta solicitada.
4. Generar slots con paso `professional.slot_granularity_minutes`.
5. Descartar slots que se solapen con citas existentes en estado `pending` o
   `confirmed`, aplicando `buffer_minutes` al final de cada cita ocupada.
6. Descartar slots que caigan dentro de cualquier `ProfessionalTimeOff`.
7. Descartar slots cuyo `slot + duration` se salga del tramo horario.

Sobre los query params `start_hour` / `end_hour` del contrato actual: pasan a ser
un **filtro opcional dentro** del horario del profesional, nunca la fuente de
verdad del rango. Si no se envían, el rango lo define `ProfessionalSchedule`. Si
se envían, se intersecan con él. Confírmame cómo funciona hoy en la inspección
antes de tocarlo.

**El contrato de respuesta de la API NO cambia.** Sigue siendo exactamente:
```json
{
  "professional_id": 1,
  "professional_name": "Dr. García",
  "date": "2026-04-20",
  "duration_minutes": 30,
  "available_slots": ["2026-04-20T08:00:00+02:00", "..."]
}
```
Esto es innegociable: n8n consume este endpoint hoy y no debe romperse ni
requerir cambios.

### 5. Serializer y filtros (aditivo)
- Añade los 4 campos nuevos a `ProfessionalSerializer`. No elimines ni renombres
  ninguno de los existentes (`user_info`, `services_detail`, `service_ids`,
  `professional_type_display` se quedan tal cual).
- Añade `is_active` y `accepts_online_booking` al FilterSet de
  `ProfessionalViewSet` con django-filter.

### 6. Tests (obligatorios, no opcionales)
Como mínimo:
- Jornada partida: dos tramos el mismo día → no salta la constraint, y el motor
  genera slots en ambos tramos y **ninguno** en el hueco del mediodía.
- Solapamiento de tramos → `ValidationError`.
- Profesional con `is_active=False` → `available_slots` vacío.
- Slot dentro de un `ProfessionalTimeOff` → excluido.
- `buffer_minutes=15` con una cita de 30 min a las 10:00 → el slot de las 10:30
  NO está disponible, el de las 10:45 sí.
- Test de contrato: el endpoint sigue devolviendo la misma forma de respuesta.

---

## Fuera de scope — NO tocar

- **Nada de clinic-workflows / n8n.** Ningún JSON de workflow.
- **El contrato de respuesta de `available-slots`.** Ni un campo más, ni uno menos.
- La lógica de cambio de status de `Appointment` (nada de `.update()` directo, y
  no toques los métodos/servicios de confirm/cancel).
- Los modelos `Patient`, `Clinic`, `Service`, `Appointment`, `Reminder`.
- Los endpoints públicos por token, el portal, ni el flujo de booking público.
- Los endpoints bulk-create / bulk-update.
- Los campos de perfil "blandos" (`license_number`, `specialty`, `bio`,
  `calendar_color`): van en una tanda posterior. **No los añadas ahora.**
- Las validaciones de pertenencia a clínica (user↔clinic, services↔clinic):
  también tanda posterior.

---

## Verificación antes de dar la tarea por terminada

Recórrelo explícitamente y dime el resultado de cada punto:
- [ ] Toda la lógica nueva está en `services.py`; views y serializers no calculan nada.
- [ ] `makemigrations` genera migraciones limpias y `migrate` corre sin errores.
- [ ] `makemigrations --check` queda limpio (sin cambios pendientes).
- [ ] Los tests nuevos pasan y **los tests existentes siguen pasando**.
- [ ] La respuesta de `available-slots` es compatible campo a campo con la anterior.
- [ ] No queda ninguna constante de granularidad o buffer hardcodeada huérfana.
- [ ] `ProfessionalTimeOff` está registrado en el admin (solo admin, sin vistas
      de panel todavía).

Al terminar, dame un resumen de los archivos tocados y las migraciones creadas.