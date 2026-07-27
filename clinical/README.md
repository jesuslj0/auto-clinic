# App `clinical`

Núcleo clínico del proyecto: la historia clínica y todo lo que cuelga de ella.
Es la capa con más implicaciones legales (datos de salud: categoría especial del
RGPD art. 9 y parte de la historia clínica según la Ley 41/2002), así que aquí el
diseño manda sobre la comodidad.

## Modelos

| Modelo | Qué es |
|---|---|
| `MedicalHistory` | Historia clínica. 1:1 con `Patient`. Se crea sola al alta del paciente. No se borra nunca. |
| `Episode` | Proceso asistencial. Agrupa visitas. Unidad sobre la que se cuenta la conservación. |
| `Visit` | Encuentro clínico que **ocurrió**. Distinta de `Appointment`. |
| `ClinicalNote` | Nota SOAP (`subjective`/`objective`/`assessment`/`plan`). Borrador → firmada. |
| `Addendum` | Añadido a una nota. De solo inserción. |
| `QuestionnaireTemplate` | Cuestionario de anamnesis como entidad lógica. Agrupa versiones; no tiene preguntas. |
| `TemplateVersion` | Versión concreta del cuestionario. Inmutable una vez publicada. Solo una vigente. |
| `Question` | Pregunta de una versión. Congelada cuando la versión se publica. |
| `QuestionnaireResponse` | Cuestionario contestado. Congela una copia literal de preguntas y respuestas. |

## Tres estados de dato

Esta capa distingue tres estados, y ante la duda entre uno y otro, gana el más
restrictivo:

- **Editable** — borradores y datos administrativos. Se modifican con normalidad.
- **Firmado / inmutable** — una nota firmada no se edita ni se borra jamás. Solo
  admite una adenda.
- **Conservado** — nada de esta capa se borra físicamente. El borrado es siempre
  lógico y la conservación se cuenta por episodio.

## Ciclo de una nota: borrador → firmada → adenda

```
ClinicalNote(status='draft')          # editable con libertad
      │  .sign(professional)
      ▼
ClinicalNote(status='signed')         # inmutable: fija firmante, fecha y hash
      │  Addendum.objects.create(...) # único cambio posible tras firmar
      ▼
Addendum (append-only)                # no altera la nota ni su hash
```

- **Firmar** fija `signed_by`, `signed_at`, calcula `content_hash` y vuelve la
  nota inmutable. El hash es `sha256:<hexdigest>` de un JSON canónico con los
  cuatro campos SOAP + el id de la nota + el id del firmante + `signed_at`. Ata
  *qué* se firmó a *quién* y *cuándo* (`clinical/hashing.py`).
- **La adenda** es la única vía de añadir algo a una nota firmada. No se edita ni
  se borra, y no toca la nota original ni su hash.

## Barrera de inmutabilidad (dos niveles)

Una nota firmada y una adenda no admiten `UPDATE` de contenido ni `DELETE`. Se
defiende a dos niveles, como la app `audit`:

1. **Modelo / ORM** — `ClinicalNote.save()` rechaza cualquier cambio sobre una
   nota que ya estaba firmada; `Addendum` usa un manager de solo inserción; el
   borrado de una nota firmada está vetado por `can_be_deleted()`.
2. **Trigger de PostgreSQL** (`migrations/0002_immutability_triggers.py`) — un
   `BEFORE UPDATE OR DELETE` que rechaza el cambio incluso en SQL crudo o desde
   `psql`. Sobre `clinical_note` bloquea todo `UPDATE`/`DELETE` de una fila con
   `status='signed'` (la transición borrador→firmada pasa porque en ese momento
   la fila aún es `draft`); sobre `clinical_addendum` bloquea todo `UPDATE`/
   `DELETE`.

## Anamnesis: cuestionarios versionados

Un cuestionario de anamnesis cambia con los años, pero lo que un paciente
contestó en 2026 tiene que poder leerse en 2036 **tal cual se contestó**. Se
resuelve con dos mecanismos que se complementan, y hacen falta los dos:

1. **Versionado.** El cuestionario lógico (`QuestionnaireTemplate`) no tiene
   preguntas: las tienen sus versiones. Publicar una versión la congela.
   Cambiar el cuestionario es **publicar una versión nueva**, jamás editar la
   anterior.
2. **Snapshot literal.** `QuestionnaireResponse.snapshot` guarda el texto de cada
   pregunta, su tipo, sus opciones y la respuesta dada. No son FK a `Question`:
   son una copia. Aunque después se despublique la versión o se borre una
   pregunta, la respuesta sigue siendo legible por sí sola.

El versionado protege del cambio *previsto*; el snapshot, también del imprevisto.

```
QuestionnaireTemplate            "Anamnesis podológica"
      │
      ├── TemplateVersion v1 (publicada)  ── Question, Question, Question
      │         └── QuestionnaireResponse ── snapshot: copia literal + respuestas
      └── TemplateVersion v2 (vigente)    ── Question, Question, Question, Question
```

- `template.new_draft_version()` clona las preguntas de la vigente para retocar
  el borrador; `version.publish()` lo congela y lo vuelve vigente, degradando la
  anterior. **Una sola versión vigente por cuestionario**, garantizado por un
  índice único parcial y no solo por el código.
- Publicar v2 **no toca ninguna respuesta de v1**. Despublicar tampoco: cada
  respuesta lleva su copia.
- Una respuesta solo se registra sobre una versión **publicada**.
- La vía de alta es `QuestionnaireResponse.record(version=…, patient=…,
  episode=…, answers={question_id: respuesta}, source=…, created_by=…)`. El
  congelado ocurre en el `save()`, así que da igual por dónde entre.

### `source` y `created_by`

`created_by` es **opcional a propósito**: de los tres canales de entrada
(`source`), solo uno tiene profesional autenticado detrás.

| `source` | Quién rellena | `created_by` |
|---|---|---|
| `professional` | El profesional en consulta | el profesional |
| `patient_web` | El paciente desde el formulario web | vacío |
| `patient_whatsapp` | El paciente por WhatsApp (vía n8n) | vacío |

`source` es lo que después permite auditar por dónde entró cada dato. Que el
agente pueda *aportar* una respuesta no le da ningún acceso de lectura a esta
capa: sigue sin haber API sobre `clinical`.

### Inmutabilidad de la anamnesis

Los mismos dos niveles, con el trigger en `migrations/0004`:

| Fila | Modelo / ORM | Trigger PostgreSQL |
|---|---|---|
| `QuestionnaireResponse` | `save()` rechaza cambiar snapshot, versión, paciente, episodio, canal, fecha y autor | `UPDATE` de contenido y `DELETE` físico bloqueados |
| `Question` de versión publicada | `save()` y `can_be_deleted()` lo impiden | `INSERT`/`UPDATE`/`DELETE` bloqueados |
| `TemplateVersion` publicada | `save()` congela `template`, `number` y `published_at` | `UPDATE` de esos campos y `DELETE` físico bloqueados |

Lo que **sí** se mueve en una versión publicada es su ciclo de vida:
`is_current`, `is_published` (retirarla) y el borrado lógico. Ninguno de los tres
altera una respuesta ya guardada.

## Separación `Appointment` / `Visit`

Son entidades distintas a propósito:

- Una **visita puede existir sin cita** (una urgencia).
- Una **cita puede no acabar en visita** (un *no-show*).
- El **agente de n8n escribe sobre `Appointment`**, pero **no tiene ningún camino
  hacia `Visit`** ni hacia nada que cuelgue de ella. Esta capa **no expone API
  REST**: es deliberado, para que el token `Api-Key` del agente no pueda alcanzar
  dato clínico alguno. La FK `Visit.appointment` es opcional y solo apunta hacia
  la capa administrativa, nunca al revés.

## Borrado lógico y cascada

Toda esta capa usa `core.models.SoftDeleteModel` (salvo `Addendum`, que es
append-only puro): `deleted_at` como única fuente de verdad, `objects` oculta los
borrados, `all_objects` los ve, y `delete()` —tanto de instancia como de
queryset— hace borrado lógico, nunca físico.

Django no cascadea el borrado lógico, así que se hace a mano:

- Borrar un `Episode` recorre y borra lógicamente sus `Visit`, y cada `Visit`
  sus `ClinicalNote` en borrador.
- Una **nota firmada veta** el borrado de su visita y de su episodio
  (`can_be_deleted()` devuelve `False`). Como consecuencia, **la cascada solo
  alcanza borradores**: una nota firmada nunca se borra, ni directa, ni por
  manager, ni por cascada.
- `MedicalHistory` no se borra nunca, ni siquiera lógicamente.

## Conservación (pendiente de fijar)

`Episode.retention_expires_at` calcula, a partir de `discharged_at`, cuándo
expira el plazo legal de conservación. Es **solo andamiaje de cálculo**: no hay
ninguna purga automática, y el número de años **no está fijado en el código**.

Vive en el setting `CLINICAL_RETENTION_YEARS` (`clinical/conf.py`), con un valor
por defecto conservador (15 años) por encima del mínimo legal, porque:

- La Ley 41/2002 (art. 17) obliga a conservar la historia un **mínimo de 5 años**
  desde el alta de cada proceso asistencial.
- Varias comunidades autónomas amplían el plazo. **Pendiente de confirmar** el
  aplicable a cada clínica antes de implementar cualquier purga.

## Auditoría

Los nueve modelos están registrados en `audit` desde `ClinicalConfig.ready()`.
Todo el texto clínico (los cuatro SOAP, `Episode.reason`, `Addendum.text`, el
`snapshot` de la respuesta) está marcado como **sensible**: el `ChangeLog` guarda
que el campo cambió, nunca su valor. El cuestionario en blanco —plantilla,
versiones y preguntas— no es dato de paciente y se audita entero, para poder
reconstruir quién publicó qué versión y cuándo. El `patient_resolver` de cada modelo sube por la cadena hasta el paciente,
de modo que todo evento queda atribuido a la ficha correcta.

Como esta capa no tiene API, la única vista que muestra dato clínico es el admin,
que instrumenta `AccessLog` (ver `clinical/admin.py`). **Cualquier endpoint nuevo
sobre esta capa debe instrumentar `AccessLog` y quedar vedado al token de n8n.**

## Datos de ejemplo (solo desarrollo)

```bash
python manage.py seed_clinical --dry-run     # enseña el plan, no escribe
python manage.py seed_clinical               # siembra
python manage.py seed_clinical --clinic 123456
```

Rellena la historia de los pacientes que ya existan: episodio cerrado con nota
firmada y adenda, episodio abierto con nota en borrador —colgando de sus citas
completadas cuando las hay— y el cuestionario «Anamnesis dental» con v1 y v2
publicadas y una respuesta por paciente.

Es repetible: los pacientes que ya tengan episodios se saltan. **Se niega a
ejecutarse con `DEBUG=False`**, y no por prudencia genérica: las notas firmadas
que crea no se pueden borrar después, ni por ORM ni por SQL.

## Tests

En `tests/clinical/` y `tests/core/test_soft_delete.py`, siguiendo la convención
del repo (`pytest.ini` recoge `tests/*/test_*.py`):

```bash
pytest tests/clinical tests/core/test_soft_delete.py
```
