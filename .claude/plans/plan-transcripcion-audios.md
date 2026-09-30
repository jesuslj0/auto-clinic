# Plan: transcripción de notas de voz

**Para:** Jesús y su Claude.
**Objetivo:** que el agente entienda las notas de voz. El audio se transcribe y
el agente contesta al texto como a cualquier mensaje, sabiendo que viene de un
audio: si algo importante no se entiende, pregunta antes de actuar.

> **Estado (30/09/2026): montado y publicado por Xexu, pendiente de probar.**
> Azure y n8n están hechos (secciones 3 y 4, que describen lo que hay). Falta la
> batería de pruebas (sección 5) y J7, que es opcional. Si algo falla en las
> pruebas, lo arregla Jesús: la sección 5 dice dónde mirar.

---

## 0. Contexto (Claude de Jesús: léelo antes de nada)

**Antes de este cambio:**

- El agente funciona por WhatsApp real con el número de prueba (ver
  `detailed-plan-test-gaena.md`).
- `WA-Media-Ingest` (id `P5hzbvgK4v6D8yAe`) descarga la foto o el audio de Meta
  y lo sube a Django, **sin guardar ejecuciones**.
- Las **fotos** llegan al agente como aviso (no ve la imagen) y las revisa Elena.
  **Eso no cambia.**
- Las **notas de voz** se guardaban, pero el paciente recibía *«Hemos recibido la
  nota de voz. El personal de la clínica la revisará…»*. **Esto es lo que
  cambia.**

**Opción C:** un subflujo nuevo, `WA-Audio-Transcribe` (id `FxmYBHucnrzXRI18`),
llamado **desde dentro de `WA-Media-Ingest`**, justo después de que Django acepte
el audio. No desde el orquestador. Motivos:

| | Por qué |
|---|---|
| Una sola descarga | `WA-Media-Ingest` ya tiene el audio descargado |
| El audio no toca el orquestador | El orquestador **sí guarda ejecuciones**: el audio de un paciente se quedaría guardado en n8n |
| A Azure solo llega lo validado | Se transcribe después de que Django valide el fichero por su contenido |
| Subflujo aparte | Reutilizable, fácil de probar y **fácil de sustituir** (ver abajo) |

**Modelo: `whisper` en Azure OpenAI, recurso `propus-openai-se` (Sweden
Central), despliegue *Standard* regional.** Unos 0,006 $ por minuto de audio.

- **Por qué no `gpt-4o-mini-transcribe`:** en Azure solo existe como *Global
  Standard*, que puede procesar los datos fuera de la UE. Son datos de salud:
  descartado. Tampoco está en `propus-openai-de` (Germany West Central, el del
  agente) en ningún tipo compatible.
- **Cuota: 3 peticiones por minuto** (el máximo que da Azure para Whisper). Si
  se agota, el subflujo reintenta 3 veces con 5 s de espera y, si no, el paciente
  recibe el mensaje de «no he podido escuchar tu nota de voz».
- ⏰ **Whisper se retira el 15/12/2026.** Hay que decidir el sustituto a primeros
  de noviembre (ver «Tareas futuras con fecha» en `PENDIENTE-PRODUCCION.md`). Solo
  hay que cambiar `WA-Audio-Transcribe`.

**Reglas para el Claude de Jesús:**

- `WA-Audio-Transcribe` y `WA-Media-Ingest` manejan audio y fotos de pacientes:
  **no pueden guardar ejecuciones**. Si hace falta depurarlos, activarlo solo un
  momento, solo con audios de prueba propios, y volver a *Do not save*.
- Ninguna clave en el repo ni en el chat.
- Nada de despliegues *Global Standard* con datos de pacientes.
- Para actualizar un workflow manteniendo su id, pegar los nodos en el lienzo del
  existente; **no importar** (crea uno nuevo con otro id y los *Execute Workflow*
  siguen apuntando al viejo).

---

## 1. El circuito

```
Orquestador
  Registrar Mensaje Entrante → ¿Tiene Adjunto? → Ingerir Adjunto (WA-Media-Ingest)
      WA-Media-Ingest
        Resolver URL en Meta → Descargar Binario → Subir a Django → Resultado
        → ¿Es Audio? (mime_type de Meta)
            ├─ no (foto) → Resultado sin Audio  (igual que antes)
            └─ sí → Preparar Audio → Transcribir Audio (WA-Audio-Transcribe)
                       WA-Audio-Transcribe
                         Preparar Fichero → Transcribir en Azure → Resultado Transcripción
                    → Resultado con Transcripción  { ok, transcript | null, transcribe_error }
  → Restaurar Contexto Mensaje  (audio con texto → is_text = true)
  → Es Mensaje Texto?
      ├─ true  → … → Preparar Contexto Agente ("[Nota de voz] …") → debounce → agente
      └─ false → Responder Solo Texto ("no he podido escuchar tu nota de voz…")
```

---

## 2. Reparto

| 👤 Xexu | 🛠️ Jesús |
|---|---|
| Azure y n8n: **hecho** el 30/09 | Pruebas con Xexu y arreglos si falla algo |
| | J7 (opcional) |

---

## 3. 👤 Azure (hecho)

- Recurso **`propus-openai-se`**, **Sweden Central**. Es aparte de
  `propus-openai-de` (el del agente) porque Whisper no está en Germany West
  Central.
- Despliegue **`whisper`**, tipo **Standard** (regional), 3 RPM.
- Credencial en n8n: **Header Auth «Azure Whisper (Suecia)»**, `api-key` = clave
  del recurso `propus-openai-se`. La puso Xexu directamente en n8n.
- Endpoint: `/audio/transcriptions`. **No** `/audio/translations`: el portal de
  Azure propone ese, y traduce todo al inglés.

---

## 4. 🛠️ n8n (hecho)

### J1 · Subflujo `WA-Audio-Transcribe` (id `FxmYBHucnrzXRI18`)

1. **Trigger** (Execute Workflow Trigger), *Accept all data*, para que llegue el
   binario.
2. **Code «Preparar Fichero»**: pone al binario el nombre `audio.ogg` o
   `audio.mp3` según su `mimeType`. **Whisper deduce el formato por la extensión**
   y el audio de Meta llega sin ella.
3. **HTTP Request «Transcribir en Azure»**:
   - **POST**
     `https://propus-openai-se.openai.azure.com/openai/deployments/whisper/audio/transcriptions?api-version=2024-06-01`
   - Header Auth → **«Azure Whisper (Suecia)»**.
   - Body **multipart-form-data**: `file` (binario `file`), `response_format=json`
     y `prompt` con vocabulario de podología (uñero, quiropodia, papiloma,
     onicomicosis…).
   - Sin campo de idioma: Whisper lo detecta solo (catalán, inglés…).
   - Timeout 60000; reintentos 3 × 5000 ms; *On Error*: Continue.
4. **Code «Resultado Transcripción»**: devuelve `{ ok: true, transcript }` o
   `{ ok: false, reason: 'azure_error' | 'vacio' }`. Nunca el audio.

Settings: **no guarda ninguna ejecución** (éxito, error, manuales y progreso).
Publicado. Tiene una sticky note que lo explica.

### J2 · `WA-Media-Ingest`

- «Resultado» devuelve `transcript: null`, para que fotos y audios tengan la misma
  forma.
- **IF «¿Es Audio?»**:
  `{{ String($('Resolver URL en Meta').first().json.mime_type || '').startsWith('audio/') }}`
- Rama true: **«Preparar Audio»** (recupera el binario de «Descargar Binario»,
  porque «Subir a Django» lo sustituye) → **«Transcribir Audio»** (Execute
  Workflow → `FxmYBHucnrzXRI18`, *On Error* Continue) → **«Resultado con
  Transcripción»** (`Resultado` + `transcript` + `transcribe_error`).
- Rama false: **«Resultado sin Audio»**, que devuelve lo de «Resultado». Existe
  para que la rama no quede vacía: un subflujo que acaba sin resultados deja al
  orquestador parado.

### J3 · Orquestador: «Restaurar Contexto Mensaje»

```js
const ctx = Object.assign({}, $('Preparar Contexto').first().json);
if (ctx.message_type === 'audio') {
  let transcript = '';
  try { transcript = String($('Ingerir Adjunto').first().json.transcript || '').trim(); } catch (e) {}
  if (transcript) { ctx.transcript = transcript; ctx.is_text = true; }
}
return [{ json: ctx }];
```

El `try` hace falta: con los textos, `Ingerir Adjunto` no se ejecuta y
referenciarlo lanza un error.

### J4 · Orquestador: «Preparar Contexto Agente»

Este nodo saca `chatInput` de `Normalizar Mensaje`, donde un audio tiene
`message` vacío. Antes de `// Guardar phone para el error`:

```js
if (normalized.message_type === 'audio') {
  let transcript = '';
  try { transcript = String($('Restaurar Contexto Mensaje').first().json.transcript || '').trim(); } catch (e) {}
  if (transcript) chatInput = '[Nota de voz] ' + transcript;
}
```

El debounce no necesita cambios: «Añadir al Buffer» guarda `ctx.chatInput`, que ya
lleva el `[Nota de voz] …`, y «Evaluar Ráfaga» lo une con los demás mensajes.

### J5 · Orquestador: prompt de «Agente IA Clinica»

Al **final** del *system message* (después de la regla 5 del registro del
historial):

```
NOTAS DE VOZ:
Si el mensaje empieza por [Nota de voz], el paciente te ha enviado un audio y lo que sigue es su transcripción automática, que puede tener errores (palabras mal entendidas, frases cortadas).
- Contéstale con normalidad, como a cualquier mensaje.
- Si algo importante no se entiende o es ambiguo (una fecha, una hora, un nombre, un servicio, un síntoma), NO lo supongas: pregúntale para confirmarlo antes de seguir. Por ejemplo: "Perdona, no te he entendido bien el día, ¿me lo confirmas?"
- Nunca crees, cambies ni canceles una cita con un dato que no esté claro en el audio: confírmalo primero.
- Si el texto no tiene sentido, pídele con amabilidad que te lo repita o te lo escriba.
- No hables de "transcripción" ni expliques cómo funciona: como mucho, di que no le has escuchado bien.
```

### J6 · Orquestador: mensaje cuando no se puede transcribir

A `Responder Solo Texto` ya solo llegan los audios que **no** se han podido
transcribir. En el mapa `avisos` de **`Responder Solo Texto` y de `Registrar
Respuesta Solo Texto`**, con el texto idéntico en los dos:

```
Perdona, no he podido escuchar bien tu nota de voz. ¿Me lo puedes escribir? 🙏
```

El audio sigue guardado en AutoClinic y Elena lo puede escuchar desde el chat.

### J7 · (Pendiente, recomendado) Que los fallos no pasen desapercibidos

Si la clave de Azure caduca, se agota la cuota o se retira Whisper, **todos** los
audios recibirán el «no he podido escucharte» y nadie se enterará. Cuando
`transcribe_error` sea `azure_error`, registrar un error con
`POST /api/agent/errors/` (el mismo que usa `Registrar Error Agente`), con
`node_name: 'Transcribir Audio'` y **sin el texto del paciente**. `vacio` no es
un error (audio en silencio).

---

## 5. Pruebas (juntos, con el número de prueba)

⚠️ **Dejar unos 20 s entre audio y audio**: con 3 peticiones por minuto, varios
audios seguidos fallan por cuota y no por el montaje.

| # | Qué se manda | Qué debe pasar |
|---|---|---|
| 1 | Audio claro: *«Hola, quería pedir cita para quitarme un uñero la semana que viene»* | Contesta como a un texto. En Chats: el audio (se puede reproducir) y la respuesta |
| 2 | Audio ambiguo: *«el martes o el miércoles, no sé, por la tarde»* | **Pregunta** qué día antes de proponer nada |
| 3 | Audio pidiendo cita para un día concreto, hasta confirmar | Repite fecha (con día de la semana) y hora, y espera el «sí» antes de crear la cita |
| 4 | Audio en silencio o con solo ruido | *«Perdona, no he podido escuchar bien tu nota de voz…»* |
| 5 | Audio en catalán | Contesta en catalán |
| 6 | Audio seguido enseguida de un texto | Una sola respuesta que tiene en cuenta los dos (ver limitación) |
| 7 | Nombre del despliegue mal escrito a propósito (luego se restaura) | Mensaje de «no he podido escuchar»; si se hizo J7, queda un error registrado |
| 8 | Una foto | **Igual que antes**: aviso y la revisa Elena |
| 9 | Un texto normal, y el chat de prueba del panel | Igual que siempre |

Comprobar también: **ni `WA-Audio-Transcribe` ni `WA-Media-Ingest` aparecen en
el historial de ejecuciones.** En la ejecución del orquestador sí se ve la
transcripción en `chatInput`, igual que cualquier texto.

### Si algo falla: dónde mirar

En *Executions* del **orquestador**, la ejecución del audio → salida de
**«Ingerir Adjunto»**:

| Salida | Qué significa |
|---|---|
| `transcript` con texto | La transcripción funciona; el problema está más adelante (J3, J4 o el prompt) |
| `transcribe_error: "azure_error"` | Falla la llamada: credencial, URL (¿`/translations`?), nombre del despliegue o cuota (429) |
| `transcribe_error: "vacio"` | Whisper no ha oído nada |
| `transcript: null` sin `transcribe_error` | No ha entrado en la rama de audio: revisar «¿Es Audio?» y el `mime_type` que devuelve Meta |
| El nodo falla o no hay salida | `WA-Media-Ingest` o `WA-Audio-Transcribe` sin publicar, o el *Execute Workflow* apunta a otro id |

Para ver qué devuelve Azure, activar un momento el guardado de ejecuciones en
`WA-Audio-Transcribe`, **solo con audios de prueba propios**, y volver a *Do not
save* al acabar.

---

## 6. Limitación conocida

El audio tarda más en llegar al debounce que un texto (descargar + subir +
transcribir). Si el paciente manda un audio y justo después «¿vale?», el texto
puede entrar antes: el orden en la respuesta conjunta saldría invertido, o, si
la transcripción tarda más de los 6 s de espera, el agente podría contestar dos
veces. No es grave. Si molesta: guardar en el buffer la hora de Meta de cada
mensaje y ordenar por ella al unirlos.

---

## 7. Fuera de alcance (fase B, Django, más adelante)

- Guardar la transcripción en AutoClinic (modelo `ChatTranscript`) y enseñarla a
  Elena bajo el audio, marcada como «Transcripción automática».
- Usarla en el historial que `should-reply` le da al agente al volver de una
  intervención humana.

Hasta entonces: el agente la tiene en su memoria (Redis Chat Memory), y Elena
tiene el audio para escucharlo. El detalle está en la sección T de
`PENDIENTE-PRODUCCION.md`.
