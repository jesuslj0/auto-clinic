# Pendiente para salir a producción con Gaena

Estado a 28/09/2026. Qué falta, dónde se hace y en qué orden, para conectar el
WhatsApp de Elena a la Cloud API oficial. Al final va el manual de la
transcripción de notas de voz.

Las fases 1, 2 y 4 del [plan de chats](PLAN-CHATS-PRODUCCION.md) están hechas y
en `main`. El debounce de mensajes partidos también (panel en `main`,
orquestador «con buffer» activo en n8n). Lo que queda es sobre todo **conectar**:
casi todo lo pendiente está en Meta y en n8n, no en Django.

**Leyenda**

- 🔴 **Bloqueante.** Sin esto no se conecta el número de Elena.
- 🟡 **Antes de salir.** Se podría salir sin ello, pero no conviene.
- 🟢 **Después.** Mejora, no impide salir.

---

## Resumen

| # | Qué | Dónde | Prioridad |
|---|---|---|---|
| M1 | Crear la app de Meta y probar todo con el **número de prueba** de Meta | Meta | 🔴 |
| M2 | Verificar el portfolio de Elena | Meta | 🟡 |
| M3 | Plantillas: redactarlas con Elena y mandarlas a revisión | Meta + Elena | 🔴 |
| M4 | Token permanente (System User) | Meta | 🔴 |
| M5 | Webhook: URL, verify token y suscripción a `messages` | Meta + n8n | 🔴 |
| M6 | Método de pago en la WABA | Meta | 🔴 |
| M7 | Migrar el número de Elena (el último paso) | Meta + Elena | 🔴 |
| N1 | Enviar por Graph API, no por WaAPI | n8n | 🔴 |
| N2 | Quitar `Responder a Webhook Postman` del camino real | n8n | 🔴 |
| N3 | Webhook: responder a la verificación `GET` de Meta | n8n | 🔴 |
| N4 | Guardar el `wamid` de las respuestas del agente | n8n | 🟡 |
| N5 | Reenviar los `statuses` de Meta a Django (✓✓) | n8n | 🟡 |
| N6 | Enganchar `WA-Media-Ingest` (fotos y audios) | n8n | 🟡 |
| N7 | Recordatorio de cita por plantilla | n8n | 🔴 |
| N8 | Respuesta a fotos: cambiar el "solo entiendo texto" | n8n | 🟡 |
| D1 | Fase 3: plantillas en el panel (en curso) | Django | 🔴 |
| D2 | Pasar la suite completa de tests | Django | 🔴 |
| D3 | Despliegue: migraciones y variables de entorno | Django / servidor | 🔴 |
| D4 | Notas de voz Ogg/Opus en el Safari del iPhone de Elena | Django | 🟡 |
| T | Transcripción de notas de voz | n8n + Django | 🟡 (ver manual) |
| E1 | Elena: copia de sus chats antes de migrar | Elena | 🔴 |

---

## Orden recomendado

No migréis el número de Elena para probar. **El número de prueba que da Meta
sirve para probarlo todo antes**, sin tocar nada suyo:

1. **M1** Crear la app y usar el número de prueba de Meta.
2. **N1, N2, N3, M4, M5** Cambiar n8n a la Cloud API y conectar el webhook, contra el número de prueba.
3. **Prueba completa** con el número de prueba: texto, ráfaga de mensajes, foto, audio, ✓✓, recordatorio con plantilla.
4. **M3** En paralelo desde el primer día: plantillas redactadas con Elena y enviadas a revisión.
5. **D1, D2, D3** Desplegar lo de Django.
6. **E1, M7** Solo cuando todo lo anterior funcione: copia de sus chats y migración del número.

Lo que más puede retrasar "esta semana" no es el código: es **la aprobación de
las plantillas** (Meta puede tardar y rechazar) y, si se hace, **la
verificación del portfolio**. Por eso las plantillas van en paralelo desde el
principio.

---

## Meta

### M1 🔴 App de Meta y número de prueba

- Crear la app en Meta for Developers con el producto WhatsApp.
- **Crearla dentro del portfolio de Elena**, no del de Propus. Si la app y la
  WABA son del mismo negocio, no hace falta App Review ni que Propus esté
  verificada. Comprobarlo al crearla.
- Meta da un **número de prueba** gratuito que puede escribir a unos pocos
  números verificados (los vuestros). Trae ya aprobada una plantilla de ejemplo,
  `hello_world`, que sirve para probar el envío de plantillas.
- Con ese número se prueba todo el circuito de n8n y Django sin tocar el
  WhatsApp de Elena.

### M2 🟡 Verificar el portfolio de Elena

- A nombre de Gaena, con sus documentos (no los de Propus/Iberium).
- Sin verificar funciona, con un límite de 250 conversaciones iniciadas por la
  clínica al día. Para una podóloga sola no es un problema, pero quita topes y
  tarda unos días: mejor empezarlo ya.

### M3 🔴 Plantillas

- Redactar con Elena las dos imprescindibles (ya tiene el borrador):
  1. **Recordatorio de cita**: nombre, clínica, fecha y hora.
  2. **Comodín** para reabrir la conversación: nombre y clínica.
- Crearlas en el WhatsApp Manager de la WABA, **categoría Utilidad**, y
  mandarlas a revisión.
- Una plantilla aprobada no se edita: si hay que cambiarla, se crea otra.
- Cuando estén aprobadas, darlas de alta en AutoClinic (D1) con el **nombre y el
  idioma exactos**.

### M4 🔴 Token permanente

- El token que aparece en el panel de la app **caduca en 24 horas**. Para
  producción hace falta el de un **System User** del Business Manager de Elena,
  con acceso a la app y a la WABA, y los permisos
  `whatsapp_business_messaging` y `whatsapp_business_management`.
- Ese token es el que se guarda en la configuración de la clínica en AutoClinic
  (Agente → Meta).

### M5 🔴 Webhook

- URL: la de `WHATSAPP_WEBHOOK_URL` (el webhook global de n8n, que resuelve la
  clínica por el `phone_number_id`).
- Verify token: el de la clínica en AutoClinic.
- Suscribirse al campo **`messages`** (trae tanto los mensajes como los
  `statuses`).
- Depende de N3: Meta hace un `GET` de verificación al guardar la URL.

### M6 🔴 Método de pago

- Las plantillas (el recordatorio) son mensajes que inicia la clínica y Meta los
  cobra. Comprobar en la WABA si pide método de pago antes de dejar enviarlas;
  normalmente sí.

### M7 🔴 Migrar el número de Elena

- El último paso, cuando todo funcione con el número de prueba.
- Antes: **E1** (copia de sus chats). El número se borra de la app de WhatsApp
  Business del móvil y ya no se puede usar ahí.
- Tras la migración: nombre para mostrar pendiente de aprobación por Meta.

---

## n8n

Todo esto va en el orquestador activo («WA-Inbound-Orchestrator con buffer»).
**Trabajad en un duplicado** y cambiadlo por el activo cuando funcione con el
número de prueba.

### N1 🔴 Enviar por la Graph API, no por WaAPI

Hoy `Enviar Mensaje WhatsApp` y `Responder Solo Texto` usan el formato de
WaAPI (`.../client/action/send-message` con `chatId: <tel>@c.us`). Con la Cloud
API no funcionan. Tienen que pasar a:

```
POST https://graph.facebook.com/<versión>/<phone_number_id>/messages
Authorization: Bearer <whatsapp_token>

{
  "messaging_product": "whatsapp",
  "to": "<teléfono del paciente>",
  "type": "text",
  "text": { "preview_url": false, "body": "<mensaje>" }
}
```

Es el mismo formato que ya usa Django para los mensajes del staff
(`agent/whatsapp.py`, `send_text()`).

### N2 🔴 Quitar `Responder a Webhook Postman` del camino real

Ahora el webhook de WhatsApp responde "Immediately", pero en la rama real
(`¿Es Test?` → false) sigue este nodo antes de `Enviar Mensaje WhatsApp`. Un
nodo de respuesta sin un webhook que lo espere puede dar error **y cortar el
flujo antes de enviar la respuesta al paciente**. Conectar `¿Es Test?` (false)
directamente a `Enviar Mensaje WhatsApp` y borrar ese nodo. `Webhook Test` y
`Responder Webhook Test` no se tocan: el chat de prueba del panel los sigue
usando.

### N3 🔴 Verificación `GET` del webhook

`Webhook WhatsApp` solo acepta `POST`. Al configurar la URL (M5), Meta hace un
`GET` con `hub.mode`, `hub.verify_token` y `hub.challenge`, y espera recibir
`hub.challenge` tal cual. Comprobar si otro workflow ya lo atiende; si no,
añadir un Webhook `GET` en el mismo path que compruebe el token y devuelva el
`challenge`.

### N4 🟡 Guardar el `wamid` de las respuestas del agente

`Registrar Mensaje Saliente` y `Registrar Respuesta Solo Texto` no leen el id
que devuelve la Cloud API (`messages[0].id`). Sin él, las respuestas del agente
no reciben ✓✓. Mandarlo como `wa_message_id`.

### N5 🟡 Reenviar los `statuses`

`Normalizar Mensaje` descarta con `skip` los webhooks que traen
`value.statuses`. Reenviarlos a `POST /api/agent/messages/status/` con
`id` → `wa_message_id`, `status`, `timestamp` y `errors[0].title` → `error`.

### N6 🟡 Enganchar `WA-Media-Ingest`

- En `Normalizar Mensaje` (rama Meta), sacar `msg.image.id` o `msg.audio.id` como
  `media_id`. Las notas de voz llegan como `audio`.
- Asegurar que `Registrar Mensaje Entrante` manda el `message_type` real
  (`image`, `audio`). Django rechaza el adjunto si el mensaje está como `text`.
- Tras `Registrar Mensaje Entrante`, si hay `media_id`, llamar a
  `WA-Media-Ingest` con el `id` que devuelve Django, el `media_id`, el
  `whatsapp_token` y el `django_auth_header`.

### N7 🔴 Recordatorio de cita por plantilla

Con la Cloud API, el recordatorio (el flujo que lee
`GET /api/appointments/pending-reminders/`) casi siempre sale fuera de la
ventana de 24 h, así que **tiene que ir como plantilla**. Cuerpo del envío:

```json
{
  "messaging_product": "whatsapp",
  "to": "<teléfono>",
  "type": "template",
  "template": {
    "name": "<nombre exacto en Meta>",
    "language": { "code": "<idioma exacto, p. ej. es>" },
    "components": [{
      "type": "body",
      "parameters": [
        { "type": "text", "text": "<nombre>" },
        { "type": "text", "text": "<clínica>" },
        { "type": "text", "text": "<fecha>" },
        { "type": "text", "text": "<hora>" }
      ]
    }]
  }
}
```

El nombre de la plantilla no debería ir escrito a fuego en n8n: que lo lea de
Django (ver D1). Mientras se prueba con el número de prueba, se puede usar
`hello_world`.

### N8 🟡 Respuesta a las fotos

Hoy todo lo que no es texto recibe "de momento solo puedo procesar mensajes de
texto". Con las fotos guardándose y visibles para Elena, ese mensaje ya no es
verdad. Decidir el texto (por ejemplo, que la foto le ha llegado a la clínica y
la revisará Elena). Las notas de voz tienen su propio circuito (ver T).

---

## Django

### D1 🔴 Fase 3: plantillas en el panel (en curso)

Lo que Jesús tiene entre manos. Decidido:

- Quinto apartado en Configuración → Agente: **«Plantillas»**.
- Alta a mano (sin sincronizar con Meta en la v1). El formulario tiene que
  avisar de que el nombre y el idioma deben coincidir exactos con Meta.
- Estados: **borrador, en revisión, aprobada, rechazada** (con motivo).
- Variables **autocompletadas y editables** antes de enviar, de una lista
  cerrada: nombre de pila, fecha y hora de la cita, profesional, nombre de la
  clínica. **Nada clínico.** Si queda una vacía, no deja enviar.
- En el hilo, con la ventana cerrada, selector **solo con las aprobadas**.
- `send_template()` en `agent/whatsapp.py`, que traduzca a un mensaje claro el
  error de Meta cuando la plantilla no existe o está pausada.
- Que n8n pueda consultar qué plantilla usar para el recordatorio (N7).

### D2 🔴 Suite completa de tests

Los cambios del debounce en el panel nunca se llegaron a pasar con la suite
entera. Pasarla completa antes de desplegar:

```bash
POSTGRES_HOST=localhost pytest
```

### D3 🔴 Despliegue

- Migraciones pendientes de `agent` (adjuntos, trigger, estados de entrega,
  perfil del agente y las de la fase 3).
- En el `.env` de producción: las `R2_*` (obligatorias desde la fase 2) y
  `AGENT_ERRORS_API_KEY` (el Error Handler de n8n).
- Comprobar que el servidor sirve con Daphne y que `CHANNEL_LAYERS` apunta a
  Redis, o el tiempo real no funciona.

### D4 🟡 Notas de voz en el iPhone de Elena

Las notas de voz se guardan en Ogg/Opus, que es lo que manda WhatsApp. Comprobar
que suenan en el Safari del iPhone de Elena. Si no suenan, habría que
convertirlas a AAC o MP3 al recibirlas. Es el único punto de audio que de
verdad importa para salir: si no suenan y no hay transcripción, Elena no tiene
forma de saber qué le han dicho.

### E1 🔴 Elena: copia de sus chats

Antes de migrar el número, que Elena exporte o haga copia de sus conversaciones.
Al pasarlo a la Cloud API deja de funcionar en su app.

---

# Manual: transcripción de notas de voz

## Qué problema resuelve

Hoy, si un paciente manda una nota de voz, el agente no la entiende y contesta
con un mensaje genérico. Muchos pacientes mayores usan casi solo audios. Con la
transcripción, el agente recibe el texto del audio y contesta como a cualquier
mensaje, y Elena puede **leer** en el panel qué dijo el paciente sin tener que
escucharlo.

## Prioridad

🟡 **No es bloqueante para salir**, siempre que D4 esté resuelto (que Elena
pueda escuchar los audios en su iPhone). Si la salida es esta semana y va
justa, se sale sin transcripción y se añade la siguiente. El paciente recibe el
mensaje genérico y Elena escucha el audio en el panel.

## Decisiones de diseño

**1. Se transcribe en n8n, no en Django.** Todas las llamadas a IA están ya en
n8n (con las credenciales de Azure). Django no habla con ningún proveedor de IA
y así sigue. Además, el agente necesita el texto antes de contestar, y eso
ocurre en el flujo de n8n.

**2. Se transcribe dentro de `WA-Media-Ingest`**, que es el subflujo que ya
descarga el audio y que **no guarda ejecuciones**. El audio no se queda en n8n.

**3. Orden: primero se sube a Django, después se transcribe.** Django valida el
fichero por su contenido (que de verdad sea un Ogg o un MP3) antes de nada. Así
a Azure nunca le llega algo que no haya pasado la validación.

**4. Azure OpenAI en la UE, como el agente.** El audio es dato de salud. Usar el
mismo recurso de Azure que ya procesa los mensajes, con un despliegue de
`gpt-4o-mini-transcribe` (o `gpt-4o-transcribe` / `whisper`). Estos modelos
están disponibles en regiones UE (Sweden Central y France Central, entre
otras); comprobar en el portal que el despliegue se puede hacer en la región del
recurso actual.

**5. La transcripción se guarda aparte, en un modelo nuevo.** `ChatAttachment`
es irremplazable (un trigger de la base de datos bloquea cualquier `UPDATE`),
así que no se le puede añadir un campo después. Un modelo propio,
`ChatTranscript`, de una sola escritura, siguiendo el mismo patrón.

**6. Se guarda y se enseña en el panel.** Si el agente contesta algo raro, Elena
tiene que poder ver qué entendió. En la burbuja del audio, bajo el reproductor,
el texto marcado como **«Transcripción automática»**, porque puede tener
errores.

**7. No se fuerza el idioma.** La batería de pruebas incluye mensajes en catalán
e inglés. Se deja que el modelo lo detecte.

## Flujo completo

```
Paciente manda nota de voz
  → Webhook WhatsApp
  → Normalizar Mensaje          (message_type: audio, media_id: msg.audio.id)
  → Registrar Mensaje Entrante  (Django crea el ChatMessage de tipo audio)
  → Es Mensaje Texto? → false
  → ¿Es Audio? → true
  → WA-Media-Ingest (transcribir: sí)
       1. Descarga el audio de Meta
       2. POST /api/agent/messages/<id>/media/         → Django valida y guarda
       3. POST a Azure, transcripción                  → texto
       4. POST /api/agent/messages/<id>/transcript/    → Django guarda el texto
       5. Devuelve { ok, transcript }
  → ¿Transcripción OK?
       ├─ sí → «Mensaje desde Audio» → sigue por Buscar Paciente → … → debounce → agente
       └─ no → responder "No he podido escuchar bien tu audio, ¿me lo escribes?"
```

## Django

### 1. Modelo `ChatTranscript` (`agent/models.py`)

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID | |
| `attachment` | OneToOne → `ChatAttachment`, `related_name='transcript'` | Solo adjuntos de tipo audio |
| `text` | TextField | La transcripción literal |
| `engine` | CharField | Modelo o despliegue que la hizo, p. ej. `gpt-4o-mini-transcribe` |
| `language` | CharField, blank | Idioma detectado, si Azure lo devuelve |
| `created_at` | DateTimeField, auto_now_add | |

Mismo patrón que `ChatAttachment`:

- `save()` de una fila existente lanza una excepción.
- Migración con trigger que bloquea cualquier `UPDATE` (como `agent/0009`).
- Se borra solo por la cascada si se borra la conversación.

### 2. Auditoría (`agent/apps.py`)

Registrar el modelo con el texto como sensible, igual que el cuerpo de los
mensajes:

```python
audit.registry.register(ChatTranscript, sensitive=['text'])
```

### 3. Servicio (`agent/media.py` o `agent/transcripts.py`)

`attach_transcript(message, text, engine, language='')`:

- El mensaje tiene que tener adjunto y ser de tipo audio. Si no → `ValidationError`.
- Texto vacío o solo espacios → `ValidationError`.
- Si ya tiene transcripción → `TranscriptAlreadyAttached` (no se reemplaza).
- Bloquea el mensaje con `select_for_update()`, igual que `attach_media`, por si
  n8n reintenta.
- Al terminar, `broadcast_message(message)` para que el panel repinte la burbuja.

### 4. Endpoint

`POST /api/agent/messages/<id>/transcript/`, acción del `ChatMessageViewSet`,
al lado de `media`:

- Permiso `IsAgentClinicKey` (solo n8n). Mensaje de otra clínica → 404.
- Cuerpo JSON: `{ "text": "...", "engine": "...", "language": "..." }`.
- `201` guardada · `400` no hay audio o el texto está vacío · `409` ya tenía.
- La respuesta no devuelve el texto (n8n ya lo tiene).

### 5. Panel

- `templates/agent/_message_bubble.html`: si el adjunto de audio tiene
  transcripción, debajo del reproductor, con la etiqueta «Transcripción
  automática» en letra pequeña y el texto con los tokens de siempre
  (`text-content-muted`).
- `should-reply` (`agent/views.py`): el historial que se le da al agente usa el
  `body` del mensaje, que en un audio está vacío. Para los audios con
  transcripción, usar el texto transcrito. Si no, al volver de una intervención
  de Elena el agente no sabrá qué dijo el paciente en ese audio.

### 6. Tests (`tests/agent/test_chat_transcript.py`)

- Guarda la transcripción de un audio y la devuelve con 201.
- 400 si el mensaje es de texto, si es una imagen o si no tiene adjunto.
- 400 con texto vacío.
- 409 si ya tenía transcripción; la original no cambia.
- 404 con un mensaje de otra clínica.
- 403 sin la `Api-Key` del agente; el staff tampoco puede escribirla.
- `save()` de una transcripción existente lanza excepción.
- Queda en el `ChangeLog` sin el texto (campo sensible).
- La burbuja pinta la transcripción; `should-reply` la incluye en el historial.

## n8n

### En `WA-Media-Ingest`

Añadir un parámetro de entrada `transcribe` (true/false). Después de la subida a
Django, y solo si `transcribe` es true y la subida devolvió 201:

**1. Transcribir** (HTTP Request):

```
POST https://<recurso>.openai.azure.com/openai/deployments/<despliegue>/audio/transcriptions?api-version=<versión>
api-key: <credencial de Azure>
Content-Type: multipart/form-data

file: <el binario descargado de Meta>
```

- La respuesta trae `{ "text": "..." }`.
- Timeout razonable (los audios largos tardan) y `continueOnFail`: si falla, el
  flujo sigue y devuelve `ok: false`.

**2. Guardar en Django** (HTTP Request):

```
POST <DJANGO_API_URL>/api/agent/messages/<id>/transcript/
Authorization: <django_auth_header>

{ "text": "<text>", "engine": "<despliegue>" }
```

**3. Devolver** al orquestador `{ ok: true, transcript: "<text>" }`, o
`{ ok: false }` si algo falló o el texto viene vacío (un audio en silencio).

Mantener **«no guardar ejecuciones»** en este subflujo: ahora también maneja el
texto transcrito.

### En el orquestador

**1. Tras `Es Mensaje Texto?` (false)**, un IF **`¿Es Audio?`** sobre
`message_type == 'audio'`:

- **true** → `WA-Media-Ingest` con `transcribe: true` → IF **`¿Transcripción OK?`**
  - **true** → Code **«Mensaje desde Audio»**: recupera el contexto completo (como
    `Restaurar Contexto Mensaje`) y conecta a `Buscar Paciente`, uniéndose al
    camino normal. Así el audio pasa también por el debounce.
  - **false** → respuesta fija: *"No he podido escuchar bien tu audio, ¿me lo
    puedes escribir? 🙏"* (por Graph API). El audio queda guardado para Elena.
- **false** (fotos) → `WA-Media-Ingest` con `transcribe: false` → respuesta de N8.

**2. `Preparar Contexto Agente`: aquí está la trampa.** Este nodo saca
`chatInput` de `Normalizar Mensaje`, donde el mensaje de un audio está vacío.
Hay que hacer que use la transcripción cuando exista:

```js
let transcript = null;
try { transcript = $('WA-Media-Ingest').first().json.transcript; } catch (e) {}

// ...

chatInput: transcript
  ? '[Nota de voz transcrita] ' + transcript
  : normalized.message,
```

El prefijo es para que el agente sepa que viene de un audio.

**3. Añadir al system prompt del agente:**

```
NOTAS DE VOZ:
Si el mensaje empieza por [Nota de voz transcrita], el paciente te ha mandado un
audio y lo que sigue es una transcripción automática, que puede tener errores.
Contéstale con normalidad. Si algo importante no se entiende o es ambiguo (una
fecha, una hora, un nombre), pregúntalo en vez de suponerlo. No menciones la
transcripción salvo para pedirle que repita algo.
```

## Limitación conocida: audio mezclado con texto

El audio tarda más en llegar al debounce que un texto, porque antes hay que
descargarlo y transcribirlo. Si el paciente manda un audio y justo después
"¿vale?", el texto puede entrar primero en el buffer:

- El orden de la respuesta conjunta podría salir invertido.
- Si la transcripción tarda más que la espera del debounce (6 s), el agente
  puede contestar dos veces: primero al texto y luego al audio.

No es grave. Si molesta, la mejora es guardar en el buffer la hora de Meta de
cada mensaje y ordenar por ella antes de unirlos.

## Cómo probarlo

El chat de prueba del panel solo manda texto, así que la transcripción **solo se
puede probar con WhatsApp de verdad**: con el número de prueba de Meta (M1).
Casos:

1. Audio corto y claro en castellano → el agente contesta a lo que se dijo; en el
   panel se ve la transcripción bajo el audio.
2. Audio pidiendo cita con una fecha ("el jueves que viene a las diez") → el
   agente la resuelve con el calendario, como un texto.
3. Audio en catalán → contesta en catalán.
4. Audio en silencio o solo ruido → mensaje de "no he podido escucharte"; el
   audio queda en el panel.
5. Audio seguido de un texto → ver la limitación de arriba.
6. Tumbar a propósito la llamada a Azure (despliegue mal escrito) → mismo
   mensaje genérico; el audio queda en el panel.

## Documentación que hay que actualizar después

- `CLAUDE.md`, sección de adjuntos de WhatsApp: añadir `ChatTranscript`.
- `ENDPOINTS.md`: el endpoint `/transcript/`.
- `preguntas-tipo-agentetest/preguntastest.md`: quitar lo de que el agente no
  entiende notas de voz.
