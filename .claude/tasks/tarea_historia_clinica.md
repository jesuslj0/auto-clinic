# Tarea 2: capa clínica base (historia, episodio, visita, nota SOAP)

## Contexto

CRM para clínicas de podología en Django. Almacena datos de salud: categoría
especial bajo RGPD art. 9 y parte de la historia clínica bajo Ley 41/2002.

**Prerrequisito:** la tarea 1 (app `auditoria`, con `RegistroCambio` y
`RegistroAcceso`) ya está implementada. Esta tarea depende de ella y debe usarla,
no reinventarla.

Capa administrativa ya existente: `Paciente`, `Profesional`, `Servicio`, `Cita`,
`Agenda` (tramos, bajas). Existe un agente de n8n que consume la API para gestionar
citas.

Esta tarea construye el **núcleo clínico**. Es la parte con más implicaciones
legales del proyecto, así que el diseño importa más que la velocidad.

## Antes de escribir código

1. Explora el repo y resume: layout de apps, modelo de usuario, si hay DRF, WSGI o
   ASGI, convenciones de tests, cómo funciona el registry de `auditoria` y cómo se
   instrumenta la lectura de una vista.
2. Propón un plan por escrito con los ficheros a crear o tocar y el orden de
   migraciones.
3. **Espera mi visto bueno antes de implementar.**
4. Donde veas más de una opción razonable, plántemela en vez de decidir por tu
   cuenta. En concreto espero preguntas sobre: numeración de historia, borrado
   lógico, y el formato del hash de firma.

## Principio rector

Esta capa distingue tres estados de dato:

- **Editable** — datos administrativos y borradores. Se modifican con normalidad.
- **Firmado / inmutable** — una vez firmada, una nota clínica no se edita ni se
  borra jamás. Solo admite adendas.
- **Conservado** — nada de esta capa se borra físicamente. El borrado es siempre
  lógico y la conservación se cuenta por episodio.

Si en algún punto dudas entre editable e inmutable, es inmutable.

## Modelos

### `HistoriaClinica`

- Relación 1:1 con `Paciente`.
- `numero` de historia: único, estable, legible. Propónme el esquema de generación
  (correlativo, con prefijo de año, etc.) en el plan; no lo elijas tú.
- `fecha_apertura`.
- Se crea automáticamente al crear un `Paciente` (señal o método), de modo que
  nunca exista un paciente sin historia.
- No se borra nunca.

### `Episodio`

Agrupa las visitas de un proceso asistencial. Es la unidad sobre la que se calcula
el plazo legal de conservación.

- FK a `HistoriaClinica`.
- `motivo_consulta` (texto).
- `fecha_apertura`, `fecha_alta` (nullable).
- `estado`: `abierto` / `cerrado`.
- `profesional_responsable` (FK a `Profesional`, `SET_NULL`).
- Al cerrarse debe fijar `fecha_alta`. Un episodio cerrado no admite visitas nuevas
  sin reabrirse explícitamente (registra la reapertura).

### `Visita`

El encuentro clínico que realmente ocurrió. **Entidad distinta de `Cita`.**

- FK **obligatoria** a `Episodio`.
- FK **obligatoria** a `Profesional`.
- FK **opcional** a `Cita` (hay visitas sin cita previa y citas sin visita).
- `fecha_hora`.
- Motivo de que sean entidades separadas: una visita puede existir sin cita
  (urgencia), una cita puede no acabar en visita (no-show), y el agente de n8n
  escribe sobre `Cita` pero **no debe tener ningún camino hacia `Visita` ni hacia
  nada que cuelgue de ella**. Respeta esa frontera: ninguna ruta de API accesible
  al token de n8n puede leer ni escribir esta capa.

### `NotaClinica`

- FK a `Visita`.
- Cuatro campos SOAP: `subjetivo`, `objetivo`, `analisis`, `plan`.
- `estado`: `borrador` / `firmada`.
- `firmada_por` (FK a `Profesional`, `SET_NULL`), `firmada_en` (datetime nullable).
- `hash_contenido`: se calcula al firmar, sobre el contenido de los cuatro campos
  más metadatos de firma. Propónme el algoritmo y qué campos entran exactamente.

Reglas de inmutabilidad (el corazón de la tarea):

- En estado `borrador` se edita con libertad.
- Al firmar: se fija `firmada_por`, `firmada_en`, se calcula `hash_contenido` y el
  registro pasa a inmutable.
- Una nota firmada **no admite `UPDATE` en ningún campo de contenido ni `DELETE`**.
  Impleméntalo a dos niveles: validación en `save()`/modelo **y** una barrera dura
  (constraint o manager) para que no se pueda saltar desde el shell o el admin.
- El único cambio posible tras firmar es añadir una `Adenda`.
- Prohíbe también el borrado de la nota firmada vía cascada desde `Visita` o
  `Episodio`: nada de `on_delete=CASCADE` que arrastre notas firmadas.

### `Adenda`

- FK a `NotaClinica` y a su autor (`Profesional`, `SET_NULL`).
- `texto`, `creada_en`.
- Es de solo inserción, igual que los registros de auditoría: una vez creada no se
  edita ni se borra.
- Al crearse no altera la nota original ni su hash.

## Integración con `auditoria`

- Registra `HistoriaClinica`, `Episodio`, `Visita`, `NotaClinica` y `Adenda` en el
  registry de `RegistroCambio` desde el `AppConfig.ready()` de esta app.
- Marca como **sensibles** los campos de contenido clínico (los cuatro SOAP, el
  texto de adenda, el motivo de consulta) para que el log guarde solo el nombre del
  campo modificado, nunca el valor. El log no puede convertirse en una segunda copia
  sin cifrar de la historia.
- Toda vista que muestre o exporte datos de esta capa debe instrumentar
  `RegistroAcceso` con el paciente correcto. La firma de una nota es un evento que
  merece quedar registrado también como cambio.

## Borrado lógico y conservación

- Ningún modelo de esta capa se borra físicamente. Usa el patrón de borrado lógico
  que ya exista en el repo si lo hay; si no, propón uno (campo + manager que filtra)
  y bloquea `delete()` de queryset sobre estos modelos.
- Añade el andamiaje para la conservación por episodio: un campo o método que
  permita calcular, a partir de `fecha_alta`, cuándo expira el plazo legal. **No
  implementes la purga automática** y **no fijes el número de años en el código**:
  déjalo como setting con un valor por defecto conservador y un comentario indicando
  que el plazo aplicable depende de la normativa estatal (mínimo 5 años, Ley
  41/2002 art. 17) y de la posible normativa autonómica, pendiente de confirmar.

## Tests

Siguiendo las convenciones del repo:

- Crear un `Paciente` crea su `HistoriaClinica` automáticamente.
- No puede existir un paciente sin historia.
- Una nota en borrador se edita sin problema.
- Al firmar se fijan firmante, fecha y hash.
- Una nota firmada **no** se puede modificar: verifica que salta la barrera tanto
  vía `save()` como vía manager/constraint.
- Una nota firmada no se puede borrar, ni directamente ni por cascada al borrar la
  visita o el episodio.
- Una adenda no altera el hash de la nota original.
- Una adenda no se puede editar ni borrar.
- Los campos SOAP marcados como sensibles no filtran valores a `RegistroCambio`.
- Cerrar un episodio fija `fecha_alta` y bloquea visitas nuevas.
- El cálculo de expiración de conservación da el resultado esperado para una
  `fecha_alta` conocida.

## Entregables

- App clínica (nómbrala según la convención del repo) con los cinco modelos,
  managers, señales, constraints, admin (notas firmadas en solo lectura),
  migraciones y registro en `auditoria`.
- Tests.
- `README.md` de la app explicando el ciclo borrador → firmada → adenda, la barrera
  de inmutabilidad, la separación Cita/Visita y la política de conservación
  pendiente de fijar.
- Nota en `CLAUDE.md`: la capa clínica es inmutable tras firma y está vedada al
  token de n8n; cualquier endpoint nuevo sobre ella debe instrumentar
  `RegistroAcceso`.

## Fuera de alcance

- Anamnesis versionada, alertas clínicas, lesiones, procedimientos y consentimientos
  (son tareas posteriores). Diseña los modelos de forma que esas capas encajen
  encima sin rehacer nada, pero no las implementes.
- No toques la capa administrativa más allá de la relación 1:1 con `Paciente` y la
  FK opcional desde `Visita` a `Cita`.
- No des al agente de n8n ningún acceso a esta capa.
- No añadas dependencias nuevas sin preguntarme antes.
- No ejecutes migraciones contra ninguna base de datos que no sea local.
