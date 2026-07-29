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
| `ClinicalAlert` | Aviso permanente sobre un paciente (diabetes, alergias…). No se borra: se desactiva. |
| `Lesion` | Lesión localizada sobre el mapa del pie. Zona clínica + coordenadas normalizadas. |
| `LesionObservation` | Cómo estaba la lesión en una visita: medidas en mm y descripción. La unidad de la evolución. |
| `LesionAttachment` | Foto clínica de una observación. Vive en un bucket privado; se sirve con URL firmada. |
| `PerformedProcedure` | Qué se le hizo al paciente en una visita, con el nombre y el precio del catálogo congelados. |
| `ConsentTemplate` | Documento de consentimiento como entidad lógica. Agrupa versiones; no tiene texto. |
| `ConsentVersion` | Versión concreta del consentimiento. Texto inmutable una vez publicada. Solo una vigente. |
| `SignedConsent` | Consentimiento firmado. Guarda copia literal del texto y la firma en el bucket privado. |

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
  sus `ClinicalNote` en borrador y sus `PerformedProcedure`.
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

Todos los modelos de la capa están registrados en `audit` desde
`ClinicalConfig.ready()`.
Todo el texto clínico (los cuatro SOAP, `Episode.reason`, `Addendum.text`, el
`snapshot` de la respuesta, la `note` de la alerta, el `text_copy` del
consentimiento firmado) está marcado como **sensible**: el `ChangeLog` guarda
que el campo cambió, nunca su valor. Las claves de los ficheros del bucket
(`LesionAttachment.file`, `SignedConsent.signature_image`) también, porque el log
no puede ser el índice de dónde vive la foto o la firma de un paciente.

Los documentos en blanco —cuestionarios y consentimientos, con sus versiones y
preguntas— no son dato de paciente y se auditan enteros, para poder reconstruir
quién publicó qué versión y cuándo. Lo mismo con los procedimientos realizados:
todo son códigos e importes, y el importe es justo lo que hay que poder
reconstruir. El `patient_resolver` de cada modelo sube por la cadena hasta el
paciente, de modo que todo evento queda atribuido a la ficha correcta.

Como esta capa no tiene API, las únicas vistas que muestran dato clínico son el
admin y la descarga de ficheros protegidos, y las dos instrumentan `AccessLog`
(ver `clinical/admin.py` y `clinical/views.py`). **Cualquier endpoint nuevo sobre
esta capa debe instrumentar `AccessLog` y quedar vedado al token de n8n.**

## Alertas clínicas

Lo que hay que saber **antes** de tratar a un paciente: diabetes, enfermedad
vascular periférica, neuropatía, anticoagulantes, alergia al látex o a
anestésicos locales, y un `other` para lo que no encaje.

Cuelgan del **paciente**, no del episodio, a propósito: una alergia no caduca al
cerrar un proceso asistencial. Y **no se borran, se desactivan**:

```python
alert.deactivate()      # is_active = False, la fila se conserva
alert.reactivate()
alert.delete()          # ProtectedClinicalRecord: no es el camino
```

Conservar la fila es lo que permite responder después a «esto se sabía en aquel
momento», que es una pregunta de responsabilidad, no de comodidad.

La consulta que pinta la ficha vive en el manager, no repartida por las vistas:

```python
ClinicalAlert.objects.active_critical_for(patient)   # críticas vigentes, recientes primero
ClinicalAlert.objects.for_patient(p).active().critical()   # los helpers encadenan
```

Que ese bloque sea **no descartable en pantalla es cosa de la presentación**: el
modelo solo expone la consulta y no impone nada sobre cómo se pinta.

### Procedencia: `source` y `source_response`

| `source` | Qué es | `source_response` |
|---|---|---|
| `manual` | La levanta un profesional | siempre vacío |
| `derived` | La levanta el motor desde una anamnesis | obligatorio |

Cada alerta derivada apunta con `source_response` a la `QuestionnaireResponse` de
la que salió, así que siempre se puede contestar de dónde vino un aviso. La
coherencia entre ambos campos se valida en `save()`: no puede quedar una alerta
«derivada» sin decir de dónde. La FK es `PROTECT`, la procedencia no se evapora.

## Motor de derivación (anamnesis → alerta)

```python
from clinical.derivation import derive_alerts

derive_alerts(response)          # sincroniza las alertas de esa respuesta
```

Lo dispara solo `QuestionnaireResponse.record(...)`, que es la vía de alta de
cualquier anamnesis (panel, formulario del paciente o n8n). **Es una llamada
explícita, no una señal**: así el mismo camino sirve para los tres orígenes y se
ve en el código que dar de alta una anamnesis puede levantar avisos. Con
`record(..., derive=False)` se salta.

### `Question.code`: la clave de todo

Las reglas **no casan por el texto ni por el orden** de la pregunta —ambos
cambian—, sino por `Question.code`, un slug estable (`has_diabetes`,
`takes_anticoagulants`, `allergy_latex`…). Ese código **viaja congelado dentro
del snapshot**, de modo que una respuesta de hace años se sigue interpretando
aunque la pregunta viva se haya reescrito o ya no exista.

- Es opcional: una pregunta sin código es informativa y no alimenta alertas.
- Es único por versión (índice parcial); los códigos nulos no colisionan.
- Se **clona al crear una versión nueva**: es lo único que mantiene reconocible
  «la misma pregunta» de la v1 a la v2.
- Las respuestas anteriores a que existiera el campo no traen `code` y
  simplemente no casan con nada. Nada revienta.

### Las reglas son datos

En `clinical/rules.py`, como una lista de `AlertRule`. Añadir una regla es añadir
una fila y ponerle el código a la pregunta; el motor no se toca:

```python
AlertRule(question_code='has_diabetes', alert_type=ClinicalAlert.AlertType.DIABETES)
```

`evaluate_snapshot(snapshot, rules=None) -> [AlertSpec]` es **pura y sin base de
datos**: se le pasa una lista de diccionarios y devuelve qué alertas pide. Se
puede probar y afinar sin montar nada. Los matchers (`is_affirmative`,
`answer_in(...)`) cubren sí/no y preguntas de elección.

Reglas iniciales de podología, todas críticas: diabetes, enfermedad vascular
periférica, neuropatía, anticoagulantes, alergia al látex y a anestésicos
locales.

### Idempotencia y correcciones

- Una alerta derivada se identifica por **(paciente, tipo, respuesta de origen)**,
  respaldado por un índice único parcial. Derivar dos veces la misma respuesta no
  crea nada.
- **Las manuales no se tocan jamás**: todas las consultas del motor filtran por
  `source='derived'`.
- **Corregir es desactivar, nunca borrar.** Si la anamnesis nueva ya no sostiene
  una condición, la alerta anterior queda `is_active=False` con su fila intacta.
- **Reemplazo entre respuestas**: al contestar de nuevo el mismo cuestionario, se
  apagan *todas* las alertas derivadas de respuestas anteriores de ese
  cuestionario —sostengan o no las mismas condiciones— y la respuesta nueva
  vuelve a levantar las que sigan vigentes. Es más de lo que pide el mínimo, y a
  propósito: dejar vivas las que siguen sostenidas llenaría la ficha de avisos
  duplicados, uno por respuesta. Lo anterior de OTRO cuestionario no se toca.
- Lo «anterior» se mide por `filled_at`, no por el id: una respuesta posterior
  que se derive tarde no puede quedar apagada por una anterior.

## Lesiones sobre el mapa del pie

Una `Lesion` cuelga de un **episodio** y guarda su localización **dos veces, a
propósito**, porque son dos datos que envejecen distinto:

| | Qué es | Cuánto dura |
|---|---|---|
| `anatomical_zone` | El dato **clínico**: `hallux`, `first_metatarsal`, `heel`… | Para siempre |
| `x`, `y` | Solo **para pintar**: fracciones del SVG entre 0 y 1 | Hasta el próximo rediseño |

Si mañana se rediseña el SVG, las coordenadas viejas dejan de cuadrar; **la zona
sigue siendo válida**. Por eso el dato clínico no depende del dibujo, y por eso
la zona **no es texto libre**: «1er meta», «primer metatarsiano» y «MTT1» no se
pueden contar ni comparar entre visitas. Nunca derives una de la otra.

Las coordenadas son **fracciones, jamás píxeles** — el dibujo se reescala en cada
pantalla y un píxel no significa nada fuera del tamaño en que se marcó. Se
defiende en tres sitios: validadores del campo (formularios y admin), `save()`
(el ORM) y un `CheckConstraint` (la base de datos).

### Estado y localización

```python
Lesion.objects.for_view(episode, Lesion.Laterality.LEFT, Lesion.View.PLANTAR)
lesion.resolve()          # estado y fecha a la vez, que es lo único coherente
lesion.reopen()
```

- **Resuelta ⇒ con `resolved_at`; activa ⇒ sin él.** En `clean()`, en `save()` y
  en un `CheckConstraint`. Por eso el estado se mueve con `resolve()`/`reopen()`
  y no a mano: un `update()` masivo del estado chocaría con la base de datos.
- **La localización queda fija al crearse** (`episode`, `laterality`, `view`,
  `anatomical_zone`, `x`, `y`). Una lesión no se mueve: si la marca estaba mal se
  borra lógicamente y se registra otra. Lo que cambia es la evolución, no el
  sitio.
- `for_view()` es como lo pedirá el mapa: un dibujo es siempre «pie izquierdo,
  plantar», nunca «todas las lesiones».

## Seguimiento: observaciones y fotos

Una lesión no es un dato, es una **serie**. Lo que importa clínicamente no es
«hay una úlcera en el primer metatarsiano» sino si mide menos que hace tres
semanas. Por eso la lesión guarda su localización (fija) y cada visita añade una
`LesionObservation`:

```
Lesion (dónde está — fijo)
  └── LesionObservation (cómo está hoy — una por visita)
        └── LesionAttachment (la foto de ese día)
```

```python
LesionObservation.objects.create(
    lesion=lesion, visit=visit, length_mm=12.5, width_mm=8.2, depth_mm=1.5,
    description='Úlcera con bordes limpios',
)
lesion.evolution()        # la serie en orden CRONOLÓGICO, de la primera a la última
lesion.observations.all() # el orden de la ficha: la más reciente primero
```

- **Las medidas son números en campos separados** (mm), no un texto ni un JSON
  suelto: el sentido de medir una úlcera es compararla con la de la semana
  pasada, y eso exige un dato consultable. Son opcionales —no toda lesión se
  mide— pero cuando se miden, se miden igual siempre. No admiten negativos
  (validadores + `CheckConstraint`).
- **La visita tiene que ser del mismo episodio que la lesión.** Si no, la
  evolución queda repartida entre procesos asistenciales y deja de ser legible.
- **`lesion` y `visit` quedan fijos.** Corregir las medidas es normal; reasignar
  la observación a otra lesión, no. Como en `Lesion`: si se anotó donde no era,
  se borra lógicamente y se registra otra.
- `evolution()` invierte el orden a propósito y de forma explícita: leer una
  serie al revés se presta a concluir «va a peor» cuando iba a mejor.

### Fotos: bucket privado y URL firmada

Las fotos cuelgan de la **observación** y no de la lesión: una foto sin la fecha
y las medidas de ese momento no dice nada, y así queda atada a la visita en la
que se tomó.

Viven en un bucket de **Cloudflare R2** (S3-compatible) configurado como
`clinical_media` en `STORAGES`, **separado del media público**. Tres reglas:

1. **Se guarda la clave del objeto, jamás una URL.** Una URL firmada caduca en
   minutos; persistirla sería dejar un enlace de acceso a dato de salud dentro de
   la base de datos. La URL se genera en cada petición.
2. **La clave es un UUID** (`lesion-attachments/ab/<uuid>.jpg`). Nada de
   `paciente_perez_pie_izq.jpg`: el nombre de un objeto es visible para quien vea
   la clave y no puede contar qué le pasa a quién.
3. **El fichero se valida por su contenido.** La extensión y el `Content-Type`
   los pone quien sube: no son un control. `clinical/files.py` mide el tamaño,
   mira la firma de los primeros bytes y **decodifica con Pillow**; lo que se
   guarda en `mime_type`/`size_bytes`/`checksum` es el resultado de ese examen.
   Lista blanca: **JPEG, PNG y WebP**. Un PDF con extensión `.jpg`, un SVG con
   scripts o un GIF se rechazan.

La validación ocurre en `save()`, no en un formulario, para que el adjunto que
entre algún día por la vía del agente pase por el mismo filtro que el que sube el
profesional. Y hay **dos límites de tamaño**, porque hay dos niveles de
confianza: `CLINICAL_ATTACHMENT_MAX_BYTES` (10 MiB) para la consulta y
`CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL` (5 MiB) para lo que llega del paciente
por WhatsApp o por la web.

Un adjunto queda **congelado** una vez subido (fichero, tipo, tamaño, checksum,
observación y canal). Corregir es borrar lógicamente y subir otro. El borrado es
lógico y **el objeto del bucket se conserva**: sería el único borrado físico e
irreversible de toda la capa, y no se hace desde una vista.

### Servido protegido

```python
from clinical.attachments import signed_url_for

signed_url_for(attachment, request.user)   # URL firmada, o PermissionDenied
```

`GET /clinical/attachments/<public_id>/` comprueba el permiso, deja el acceso en
`AccessLog` (`download_attachment`) y **redirige** a la URL firmada; Django nunca
sirve el fichero. Detalles que no son casualidad:

- **Comprobar y firmar están en la misma función.** Separarlas dejaría a mano una
  forma de firmar sin comprobar, y esa es justo la equivocación que no cabe aquí.
- El permiso es el del **paciente**, alcanzado por la cadena adjunto →
  observación → lesión → episodio → historia → paciente. Superusuario: todas las
  clínicas. Resto: la suya. Sin clínica: nada.
- **El agente de n8n, nunca**, ni con la `Api-Key` de la clínica del propio
  paciente: `can_view_patient()` lo rechaza en la primera línea. Este endpoint no
  abre API sobre la capa clínica — es una vista de sesión del panel.
- Se identifica por `public_id` (UUID) y no por la PK: un id secuencial invita a
  tantear el de al lado, y la mera existencia de la fila ya es información.

## Consentimiento informado

Cuando un consentimiento se discute —y se discute años después— la pregunta no es
si el paciente firmó, sino **qué texto tenía delante cuando firmó**. Se responde
con los mismos dos mecanismos que la anamnesis, y hacen falta los dos:

```
ConsentTemplate                  "Consentimiento de cirugía ungueal"
      │
      ├── ConsentVersion v1 (publicada) ── text: "Se me ha informado de que…"
      │         └── SignedConsent ── text_copy: copia literal + firma (imagen)
      └── ConsentVersion v2 (vigente)   ── text: redacción nueva
```

1. **Versionado.** El documento lógico no tiene texto: lo tienen sus versiones.
   `version.publish()` la congela y la vuelve vigente, degradando la anterior;
   `template.new_draft_version()` arranca del texto vigente para retocarlo.
   **Una sola versión vigente por documento**, garantizado por un índice único
   parcial y no solo por el código.
2. **Copia literal.** `SignedConsent.text_copy` guarda el texto **entero** que se
   firmó, no solo la FK a la versión. La redundancia es deliberada y es la razón
   de ser del modelo: la FK dice de dónde salió, pero lo que prueba qué aceptó el
   paciente es la copia, y esa copia no depende de que la versión siga
   existiendo, publicada ni intacta.

Publicar v2 **no toca ninguna firma de v1**; despublicar, tampoco. La copia se
hace en el `save()` y no en la vista (`SignedConsent.sign()` es la vía normal),
así que da igual por dónde entre la firma: sale con el texto dentro.

A diferencia de la versión de cuestionario —cuyo contenido son sus `Question`s—,
aquí el documento **es** el `text`, así que el texto entra en los campos
congelados. Y una versión en borrador **no se puede firmar**: no se le hace
firmar a nadie un texto que aún puede cambiar.

### La firma es un fichero clínico más

Una firma manuscrita es dato personal pegado a un dato de salud, así que recibe
exactamente el mismo trato que una foto de lesión: bucket **privado**, clave
opaca (UUID bajo `consent-signatures/`), validación **por contenido**, se guarda
la clave y jamás una URL.

```python
GET /clinical/consents/<public_id>/signature/
```

Comprueba el permiso, deja el acceso en `AccessLog` (`download_attachment`) y
redirige a una URL firmada de vida corta. Es la misma vista base
(`ProtectedFileRedirectView`) que sirve las fotos: el control —comprobar,
registrar, redirigir— vive en un único sitio, porque repartido por vistas es
cuestión de tiempo que una se deje un paso.

Un consentimiento firmado queda **congelado** (versión, paciente, episodio,
fecha, copia del texto y fichero con su huella) y **no se borra**: el borrado es
lógico y el objeto del bucket se conserva. Segundo nivel en la migración `0011`,
como el resto de la capa.

## Procedimientos realizados: el precio, congelado

`PerformedProcedure` es la costura entre la visita y la facturación: qué se le
hizo al paciente y **cuánto valía eso ese día**.

```
Visit ── PerformedProcedure ── frozen_service_name  "Quiropodia"
             │                 frozen_price          38.00 €
             └── service ────► services.Service      (procedencia, NO el precio)
```

El catálogo (`services.Service`) es un documento vivo: los precios suben, los
servicios se renombran y algunos se retiran. Lo que se hizo en una visita, no.
Por eso el procedimiento **copia nombre y precio en el primer `save()` y no
vuelve a mirar el catálogo nunca más**. Sin eso, subir el precio de la quiropodia
reescribiría hacia atrás lo que costaron todas las del año pasado.

Es el mismo mecanismo que el `snapshot` de la anamnesis, aplicado al dinero: el
documento tiene que poder leerse tal y como se emitió.

- **La FK a `Service` es procedencia, jamás fuente de verdad del importe.** Sirve
  para agrupar y para saber de qué entrada del catálogo salió. Leer el precio de
  ahí sería exactamente el error que esto evita.
- Los importes se pueden dar **explícitos al crear** —un servicio de precio
  variable se cobra por lo que se hizo, no por el mínimo de la ficha—; lo que no
  se dé se copia del catálogo. Una vez guardados, quedan **congelados**
  (`visit`, `service`, `frozen_service_name`, `frozen_price`). Corregir un
  importe es dar de baja el procedimiento y registrar otro.
- La FK al catálogo es `DO_NOTHING` + `db_constraint=False`, como
  `MedicalHistory.patient`: retirar un servicio no arrastra ni bloquea los
  procedimientos ya hechos, y **no dispara el `UPDATE` masivo de un `SET_NULL`**,
  que se saltaría la auditoría. `catalog_service` devuelve `None` en vez de
  reventar, y el procedimiento sigue legible entero porque lo que hay que leer
  está congelado en él.
- La **zona tratada va codificada** (`affected_zone`, el mismo vocabulario que
  `Lesion.AnatomicalZone`) y no en texto libre, por el mismo motivo que en la
  lesión: hay que poder consultarla y agregarla.
- Esta capa **no factura**: registra lo que se hizo y por cuánto. Emitir la
  factura es otra cosa y vive fuera.

## Datos de ejemplo (solo desarrollo)

```bash
python manage.py seed_clinical --dry-run             # enseña el plan, no escribe
python manage.py seed_clinical                       # siembra
python manage.py seed_clinical --clinic 123456
python manage.py seed_clinical --refresh-anamnesis   # anamnesis nueva a los ya sembrados
python manage.py seed_clinical --skip-files          # sin subir nada al bucket privado
```

Rellena la historia de los pacientes que ya existan:

- episodio cerrado con nota firmada y adenda, y episodio abierto con nota en
  borrador —colgando de sus citas completadas cuando las hay—;
- el cuestionario «Anamnesis dental» con v1 y v2 publicadas y una respuesta por
  paciente. Las preguntas van **codificadas**, así que el motor de derivación
  levanta sus alertas críticas;
- **lesiones** sobre el mapa del pie con su serie de observaciones (medidas que
  cambian visita a visita, que es de lo que va el modelo) y sus fotos, una
  resuelta y las demás activas. Cada observación crea su visita de seguimiento en
  el episodio abierto, porque una observación exige una visita del mismo
  episodio;
- **procedimientos realizados** sobre esas visitas, tomando nombre y precio del
  catálogo real de la clínica. Uno lleva importe explícito distinto del catálogo:
  es el caso del servicio de precio variable, y hace visible el congelado;
- el consentimiento «Cirugía ungueal» con v1 y v2 publicadas, y **un
  consentimiento firmado** por paciente sobre la vigente.

Las fotos y las firmas van al **bucket privado** (`clinical_media`), con la misma
validación por contenido que en producción: no hay ruta especial de siembra. Sin
`R2_*` configurado, esas dos piezas se omiten con un aviso y el resto se siembra
igual; `--skip-files` hace lo mismo a propósito. Un consentimiento sin firma no
existe, así que se omite entero.

`--refresh-anamnesis` registra una anamnesis nueva sobre la versión vigente a los
pacientes que ya tienen historia, sin tocar nada de lo anterior. Es la forma de
ver el motor de alertas sobre datos ya sembrados. Si la versión vigente no tiene
códigos —sembrada antes de que existiera `Question.code`—, el comando publica una
versión nueva con ellos en vez de intentar editar la publicada, que es inmutable.

Es repetible, y lo es **por pieza**: a un paciente que ya tenga episodios no se le
duplica la historia, pero sí se le completa lo que le falte (lesiones,
procedimientos, consentimiento firmado). Así una base sembrada antes de que
existieran estos modelos se pone al día sin borrar nada, y volver a ejecutarlo no
crea nada. **Se niega a ejecutarse con `DEBUG=False`**, y no por prudencia
genérica: las notas firmadas que crea no se pueden borrar después, ni por ORM ni
por SQL.

## Tests

En `tests/clinical/` y `tests/core/test_soft_delete.py`, siguiendo la convención
del repo (`pytest.ini` recoge `tests/*/test_*.py`):

```bash
pytest tests/clinical tests/core/test_soft_delete.py
```
