# App `audit`

Infraestructura de auditoría del proyecto. Dos registros independientes:

| Modelo | Qué guarda | Cómo se puebla |
|---|---|---|
| `ChangeLog` | Toda **escritura** (alta, modificación, baja) sobre un modelo registrado | Señales `pre_save` / `post_save` / `post_delete` |
| `AccessLog` | Toda **lectura** de datos personales o clínicos | Explícitamente, vista a vista |

Son dos tablas distintas a propósito: los volúmenes difieren en un orden de
magnitud, las lecturas hay que declararlas una a una y sus políticas de
retención no van a coincidir.

Ambos son **de solo inserción**: `update()`, `delete()`, `bulk_update()` y el
`save()` de una instancia ya persistida lanzan `AuditLogImmutable`. En el admin
de Django aparecen en modo consulta, sin permisos de alta, cambio ni borrado.

> **Contexto legal.** El proyecto almacena datos de salud: categoría especial
> del art. 9 del RGPD y parte de la historia clínica según la Ley 41/2002. La
> trazabilidad de quién accede y quién modifica no es una mejora opcional.

---

## Registrar un modelo nuevo

Nada se audita automáticamente. Cada app declara sus modelos desde el
`ready()` de su `AppConfig`:

```python
# lesiones/apps.py
from django.apps import AppConfig


class LesionesConfig(AppConfig):
    name = 'lesiones'

    def ready(self):
        from audit import registry

        from lesiones.models import Lesion

        registry.register(
            Lesion,
            sensitive=['descripcion', 'diagnostico'],
            exclude=['ultima_sincronizacion'],
            patient_resolver=lambda lesion: lesion.episodio.paciente,
        )
```

- **`sensitive`** — campos de los que se registra **que cambiaron pero nunca su
  valor**: en el diff aparecen como `{"changed": true}`. Todo dato clínico y
  todo secreto va aquí. El log de auditoría no puede convertirse en una segunda
  copia sin cifrar de la historia clínica.
- **`exclude`** — campos que ni se miran. A los indicados se suman siempre
  `created_at`, `updated_at`, `modified_at`, `last_login`, `date_joined` y
  `password`.
- **`patient_resolver`** — `callable(instancia) -> Patient | None`. Solo hace
  falta cuando el modelo no es un `Patient` ni tiene un FK `patient` directo,
  que es el caso que resuelve la heurística por defecto.

Registrados hoy: `Patient`, `Appointment`, `Professional`,
`ProfessionalSchedule`, `ProfessionalTimeOff`, `User` y `Clinic`.
`AppointmentStatusHistory` queda fuera a propósito: es el log de dominio de las
transiciones de estado de una cita, no auditoría, y registrarlo duplicaría cada
cambio en las dos tablas.

### Forma del diff

```json
{
  "phone":      {"before": "+34600111222", "after": "+34600333444"},
  "notes":      {"changed": true},
  "created_at": "excluido, no aparece"
}
```

Un `save()` que no cambia nada no genera registro: llenaría el log de ruido y
escondería los cambios de verdad.

---

## Instrumentar una vista

Las lecturas no emiten señales de Django, así que hay que declararlas. **No se
instrumenta por middleware global**: registraría estáticos, health checks y
vistas administrativas, y un log donde está todo es un log donde no se encuentra
nada.

**Vista basada en clase** (`DetailView`, `ListView`) — el mixin va el primero:

```python
from audit.mixins import AccessLogMixin


class LesionDetailView(AccessLogMixin, LoginRequiredMixin, DetailView):
    model = Lesion
```

La acción se deduce (`view` en un detalle, `list` o `search` en un listado) y se
puede forzar con `access_action`. Solo se registra si la respuesta es correcta:
un 404 o un redirect de login no son accesos a datos. Se puede afinar
sobrescribiendo `get_access_patient()`, `get_access_object()`,
`get_access_result_count()` o `get_access_reason()`.

**Viewset de DRF:**

```python
from audit.mixins import AuditedViewSetMixin


class LesionViewSet(AuditedViewSetMixin, viewsets.ModelViewSet):
    ...
```

Cubre `retrieve`, `list` (distinguiendo búsqueda por el parámetro `search`) y el
`export` de `core.mixins.ExportMixin`. Las escrituras del viewset no hay que
declararlas: las capta `ChangeLog` por señales.

**Vista basada en función:**

```python
from audit.mixins import log_access_view
from audit.models import AccessLog


@log_access_view(AccessLog.Action.DOWNLOAD_ATTACHMENT,
                 patient=lambda request, pk: Adjunto.objects.get(pk=pk).paciente)
def descargar_adjunto(request, pk):
    ...
```

**A pelo**, desde donde haga falta:

```python
from audit.mixins import log_access
from audit.models import AccessLog

log_access(
    action=AccessLog.Action.PRINT,
    patient=paciente,
    obj=informe,
    reason='Solicitud del paciente',
)
```

---

## Limitación importante: operaciones en bloque

**Las operaciones en bloque del ORM no emiten señales y por lo tanto NO quedan
auditadas.** Sobre un modelo registrado, esto deja un cambio sin rastro:

```python
Patient.objects.bulk_create([...])          # ✗ sin post_save
Patient.objects.filter(...).update(...)     # ✗ sin pre_save/post_save
Patient.objects.filter(...).delete()        # ✗ sin post_delete
Patient.objects.bulk_update([...], [...])   # ✗ sin señales
```

Lo correcto es recorrer e invocar `instance.save()` / `instance.delete()`:

```python
for patient in Patient.objects.filter(...):
    patient.clinic = otra_clinica
    patient.save()
```

Sí quedan auditados los endpoints `bulk-create` y `bulk-update` de
`core.mixins`, porque llaman a `serializer.save()` en bucle y ese sí dispara las
señales.

Tampoco se capturan hoy los cambios en relaciones **ManyToMany**
(`professional.services.add(...)`), que van por `m2m_changed`. Está fuera del
alcance de esta versión.

---

## Contexto de petición

Las señales no reciben el `request`. Un middleware ligero
(`audit.middleware.AuditContextMiddleware`, colocado justo detrás de
`AuthenticationMiddleware`) guarda IP, user agent, ruta, método y el propio
`request` en un `contextvars.ContextVar`, y lo limpia en un `finally` aunque la
vista reviente.

Es `contextvars` y no `threading.local` porque el proyecto se sirve con Daphne:
bajo ASGI varias peticiones comparten hilo y un `threading.local` filtraría el
usuario de una petición a la siguiente.

El actor se resuelve de forma **perezosa**, al escribir el registro. El motivo
es DRF: cuando corre el middleware, la autenticación por `Api-Key` o por token
todavía no ha ocurrido. DRF autentica dentro de la vista y propaga el resultado
al `HttpRequest`, así que en el momento del `write` sí se ve quién es.

**Fuera de una petición** (comando de gestión, shell, tarea de Celery) el
registro se crea igual, con `user` nulo y `origin = command`. Si el proceso sabe
en nombre de quién actúa, puede decirlo:

```python
from audit.context import audit_context

with audit_context(user=usuario, origin='command'):
    paciente.save()
```

### Orígenes

| Valor | Cuándo |
|---|---|
| `web` | Panel de la clínica |
| `api` | API REST con sesión o token |
| `admin` | Admin de Django (`/admin/`) |
| `n8n` | Agente de WhatsApp, detectado por la cabecera `Authorization: Api-Key` |
| `command` | Sin petición: comandos, shell, Celery |

El agente de n8n se autentica con un `ClinicAgent`, que **no es una fila de la
tabla de usuarios**: en sus registros el FK `user` queda a nulo y la identidad
vive en `user_repr`, con la forma `n8n:<clinic_id>`.

---

## Política ante fallo

`AUDIT_FAILURE_POLICY` en settings:

- **`fail_closed`** (por defecto) — si el registro no se puede escribir, se
  lanza `AuditWriteError` y la operación se aborta. Son datos de salud: un
  cambio que después no se puede justificar es peor que una operación que falla
  ahora y se ve.
- `fail_open` — la operación se completa y el fallo se anota en el logger
  `audit` con nivel `CRITICAL`. Válvula de escape para una incidencia.

**Alcance real de `fail_closed`:** Django emite `post_save` *fuera* de la
transacción implícita de `Model.save()`. En autocommit, la fila de dominio ya
está escrita cuando se intenta el registro, así que fail-closed garantiza que la
petición termina en error y que el fallo es visible, pero **no** que la
escritura se revierta. Para que además sea atómico, la operación tiene que ir
dentro de un `transaction.atomic` explícito, o activarse `ATOMIC_REQUESTS = True`
en la configuración de la base de datos. Está sin decidir.

---

## Integridad referencial

Los FK `user` y `patient` de ambos registros son `SET_NULL` y llevan
`db_constraint=False`. Lo segundo tiene un motivo concreto: en un borrado en
cascada, el `post_delete` de un hijo se emite mientras el padre todavía existe,
de modo que el registro de esa baja puede apuntar a un usuario o a un paciente
que morirán unas filas más tarde en la misma transacción. Con la restricción
activa, escribir la auditoría haría fallar el borrado entero.

La consecuencia es que un log puede quedar apuntando a un id que ya no existe.
Es aceptable —y previsto— porque `user_repr` y `object_repr` conservan la
identidad legible. Es la misma razón por la que esos campos existen.

Ningún modelo debe apuntar a estas tablas con `on_delete=CASCADE`.

La inmutabilidad que ofrece la app es la del ORM. Frente a SQL crudo o acceso
directo a la base de datos no protege nada: para eso hacen falta permisos del
rol de PostgreSQL o un trigger `BEFORE UPDATE OR DELETE`. **Pendiente.**

---

## Retención — pendiente de fijar

No hay política de retención implementada y **hay que decidirla**. Los plazos no
son libres:

- La Ley 41/2002 (art. 17) obliga a conservar la historia clínica un mínimo de
  **5 años** desde el alta de cada proceso asistencial; varias comunidades
  autónomas amplían el plazo.
- El RGPD exige lo contrario para lo que ya no haga falta: limitación del plazo
  de conservación (art. 5.1.e).

Los dos registros no tienen por qué compartir plazo: `AccessLog` crece mucho más
rápido y su valor probatorio decae antes que el de `ChangeLog`. Cuando se
decida, la purga debe ser un comando de gestión explícito y auditado, no un
`queryset.delete()` —que además aquí lanza excepción a propósito—.

---

## Tests

En `tests/audit/`, siguiendo la convención del repo (`pytest.ini` solo recoge
`tests/*/test_*.py`):

- `test_change_log.py` — alta/modificación/baja, diff, enmascarado de campos
  sensibles, modelos no registrados, orígenes y política de fallo.
- `test_access_log.py` — mixins de CBV y de viewset, y la función de bajo nivel.
- `test_immutability.py` — las cuatro vías de modificación, todas cerradas.
- `test_context.py` — aislamiento del contexto entre peticiones y usuarios.

```bash
pytest tests/audit
```
