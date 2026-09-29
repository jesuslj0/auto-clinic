# Plan detallado: primera prueba real del agente con Gaena

**Para:** Jesús y su Claude. **Con:** Xexu, a la vez.
**Objetivo de hoy:** escribir desde nuestro WhatsApp al número de prueba de Meta
y que el agente conteste **como en producción**, con los datos reales de Gaena
(servicios, profesionales, base de conocimiento, agenda).

> **Todo lo que es de Meta lo configura Xexu** (app, webhook, tokens,
> suscripciones). Jesús no necesita entrar en Meta. Las tareas de cada uno van
> marcadas con 👤 **Xexu** o 🛠️ **Jesús**.

---

## Estado (30/09)

**✅ El agente funciona por WhatsApp real** desde el número de prueba, de
extremo a extremo: alta de paciente nuevo, crear cita, listar citas, cancelar y
respuesta humana desde el panel. **Todo el guion de la sección 5 probado; solo
falta conectar las imágenes** (`WA-Media-Ingest`, N6/N8).

| Paso | Estado |
|---|---|
| X2 – X5 | ✅ (el flujo real funciona, así que Gaena tiene el Phone Number ID, la URL está verificada y la suscripción a `messages` hecha) |
| J0 – J6 | ✅ Hechos, con dos cambios respecto al plan (ver J0 y J3) |
| J1 | ✅ Y además se desactivó `WA-Post-Visit-Followup` |
| Clave maestra | ✅ Arreglado un 403 de `agent-config` (ver sección 7) |
| Prueba conjunta | ✅ Todo menos las fotos (11), pendiente de conectar imágenes |
| **Pendiente** | Conectar imágenes (N6/N8) · limpieza (sección 8) · X6 token permanente |

---

## 0. Contexto (Claude de Jesús: léelo entero antes de nada)

**Qué es AutoClinic y dónde está todo:** ver `CLAUDE.md`. Lo pendiente para
producción está en `.claude/plans/PENDIENTE-PRODUCCION.md` (las tareas se citan
aquí por su código: N1, M5…).

**Hasta hoy**, el agente solo se ha probado desde el chat de prueba del panel
(Agente → Chat), que entra en n8n por el webhook `whatsapp-test`. **Nunca ha
recibido un WhatsApp de verdad.**

**Lo que ya hizo Xexu (29/09):** creó la app de Meta *AutoClinic Gaena* en el
portfolio de Gaena, con un número de prueba, y mandó una plantilla de ejemplo a
su móvil y al de Jesús: llegó a los dos. Los dos móviles están dados de alta como
destinatarios del número de prueba.

**El camino que vamos a encender:**

```
Móvil (Jesús/Xexu) ──WhatsApp──► +1 555 156 9032 (número de prueba)
   ► Meta ──POST──► n8n: webhook `whatsapp-inbound`
   ► WA-Inbound-Orchestrator con buffer
        Normalizar Mensaje → Cargar Config Clínica (busca la clínica por phone_number_id)
        → Preparar Contexto → Registrar Mensaje Entrante (Django) → … → debounce 6 s
        → Agente IA Clinica → Formatear respuesta → ¿Es Test? (false)
        → Enviar Mensaje WhatsApp ──► Meta (Graph API) ──► Móvil
```

**Hoy fallan tres cosas de esa cadena:**

1. Meta no sabe a qué URL avisar (el webhook no está apuntado).
2. Ninguna clínica de AutoClinic tiene el número de prueba, así que n8n no
   encuentra la clínica.
3. n8n envía las respuestas en formato **WaAPI**, que Meta no entiende.

**Reglas para el Claude de Jesús:**

- El workflow está **publicado y activo**. Copia de seguridad antes de tocar
  nada (paso J0).
- Si tienes el MCP de n8n: primero leer y proponer; aplicar solo con el OK de
  Jesús.
- **Ningún token en el repo ni en el chat.** El token de Meta lo pone Xexu en
  AutoClinic; el token de verificación se pasan por privado.
- El JSON del workflow que conocíamos es de hace días y Jesús lo ha tocado
  después (hay nodos nuevos, como `Cargar Perfil Agente`). **Comprueba los
  nombres y las expresiones en la versión actual** antes de aplicar nada.

---

## 1. Decisión: se prueba con la clínica de Gaena

Queremos ver al agente con datos reales, así que se usa la clínica de Gaena y
no una de demo. Eso tiene consecuencias, y hay que asumirlas a propósito:

| Qué pasará | Cómo lo controlamos |
|---|---|
| Nuestras conversaciones aparecen en **Chats** de Gaena | Avisar a Elena antes (X1) |
| El agente nos dará de alta como **pacientes** de Gaena | Nombres con «PRUEBA» delante, email inventado o vuestro |
| Las citas de prueba **ocupan huecos reales** de su agenda | Pedir cita a 2-3 semanas vista y **cancelarla en la misma prueba** |
| Si hay un flujo de recordatorios activo, puede intentar usar el número de prueba | Pausarlo mientras se prueba (J1) |

**Plantillas de recordatorio: hoy no se prueban.** No están aprobadas (se
redactan con Elena) y el flujo aún no envía plantillas (N7).

---

## 2. Reparto y puntos de encuentro

Trabajamos en paralelo. Hay tres momentos en que uno espera al otro:

| | 👤 Xexu (Meta + AutoClinic) | 🛠️ Jesús (n8n) |
|---|---|---|
| **Arranque** | X1 avisar a Elena · X2 configurar Gaena · X3 token de verificación | J0 copia · J1 recordatorios |
| **🔗 A** | Le pasa a Jesús el token de verificación por privado | Lo necesita para J2 |
| | | J2 webhook GET |
| **🔗 B** | X4 verificar la URL en Meta | Avisa a Xexu cuando J2 pase su prueba |
| | | J3 IF · J4 quitar nodo · J5 envío a Meta · J6 publicar |
| **🔗 C** | X5 suscribirse a mensajes | Avisa a Xexu cuando J6 esté |
| **Juntos** | Prueba (sección 5) | Prueba (sección 5) |
| **Después** | X6 token permanente | — |

---

## 3. Datos

| Dato | Valor |
|---|---|
| Número de prueba | `+1 555 156 9032` |
| Phone Number ID | `1340751929128708` |
| Id de la WABA de prueba | `799132616628033` |
| URL del webhook | `https://n8n.alt4ir.online/webhook/whatsapp-inbound` |
| Token de verificación | Lo inventa Xexu (X3). **Por privado, nunca aquí.** |
| Token de acceso de Meta | Lo genera y lo guarda Xexu en AutoClinic. **Nunca aquí.** |
| Versión de la API de Meta | `v26.0` |

---

## 4. Pasos

### 👤 X1 · Avisar a Elena (Xexu)

Que hoy verá en **Chats** conversaciones de prueba desde un número +1 555, que
aparecerán pacientes «PRUEBA» y alguna cita que se cancela en el momento. Que no
conteste a esas conversaciones salvo que lo pidamos.

### 👤 X2 · Configurar la clínica de Gaena en AutoClinic (Xexu)

En AutoClinic, con usuario administrador de Gaena → **Agente → Configuración**:

1. **Antes de cambiar nada, apunta los valores que haya ahora** (por si hay que
   volver atrás).
2. Credenciales:
   - **Phone Number ID:** `1340751929128708`
   - **Token:** el temporal de Meta («Paso 1. Probar» → «Generar
     identificador»). Caduca en 24 h; el permanente llega en X6.
3. Webhook: **token de verificación** = el de X3.
4. Comprueba que el **agente está activado** para Gaena (interruptor general en
   Chats).
5. **Ninguna otra clínica puede tener `1340751929128708`.** n8n busca la clínica
   por ese número y, si hay dos, la búsqueda falla. (La clínica de demo usa
   `100000000000001`, no choca.)

### 👤 X3 · Token de verificación (Xexu)

Inventa una cadena larga y aleatoria. Va en tres sitios: AutoClinic (X2), n8n
(J2) y Meta (X4). Pásasela a Jesús **por privado**. → **🔗 A**

### ✅ 🛠️ J0 · Copia de seguridad e inspección (Jesús)

> **Hecho.** Copia en n8n: `WA-Inbound-Orchestrator con buffer (backup 2026-09-29)`
> (inactiva). **El punto 3 no se puede hacer así:** el webhook estaba en
> «Using 'Respond to Webhook' Node» y, puesto en *Immediately*, n8n rechaza cada
> POST con `500 Unused Respond to Webhook node found` (desde el camino común se
> alcanzan los Respond del chat de prueba). Se dejó en «Using 'Respond to
> Webhook' Node» con un nodo nuevo **`Responder 200 a Meta`** justo detrás del
> webhook: Meta recibe el 200 en ~0,3 s, igual que con *Immediately*. **No volver
> a ponerlo en Immediately.**

1. En `n8n.alt4ir.online`, workflow **`WA-Inbound-Orchestrator con buffer`** →
   `...` → **Download**. Guárdalo fuera del repo, con fecha.
2. Localiza en la versión actual estos nodos, que son los que se tocan:
   `Webhook WhatsApp`, `Normalizar Mensaje`, `Cargar Config Clínica`,
   `Preparar Contexto`, `¿Es Test?`, `Responder a Webhook Postman`,
   `Enviar Mensaje WhatsApp`, `Responder Solo Texto`,
   `Registrar Mensaje Saliente`.
3. Confirma que `Webhook WhatsApp` responde **Immediately** (se cambió hace
   días). Si no, cámbialo.

### ✅ 🛠️ J1 · Recordatorios (Jesús)

> **Hecho.** Los flujos de recordatorios (`WA-Reminder-24h-Scheduler`,
> `WA-Reminder-3h-Followup-Scheduler`) están inactivos. Se desactivó también
> **`WA-Post-Visit-Followup`**, que escribe a diario a las 10:00 a los pacientes
> con cita el día anterior (ver sección 8 antes de reactivarlo).

Si hay un workflow de recordatorios de cita **activo** que envíe por WhatsApp,
**desactívalo** mientras dura la prueba. Con Gaena apuntando al número de
prueba podría intentar mandar recordatorios a pacientes reales desde él (Meta
los rechazaría: el número de prueba solo escribe a los 5 destinatarios
verificados). Si no hay ninguno activo, nada.

### ✅ 🛠️ J2 · Webhook GET para la verificación de Meta (Jesús) — N3

> **Hecho** como se describe (GET y POST conviven en el mismo path sin
> problemas). `Webhook Verificación Meta` lleva `onError: continueRegularOutput`,
> como los otros dos webhooks. Comprobado con curl: token malo → 403, sin
> parámetros → 403, token correcto → devuelve el challenge (probado con un token
> provisional; el real lo pegó Jesús después y el provisional ya da 403).

Al guardar la URL, Meta hace un `GET` con `hub.mode`, `hub.verify_token` y
`hub.challenge`, y espera recibir `hub.challenge` tal cual. Hoy solo hay un
webhook `POST`.

Añade al mismo workflow:

1. **Webhook** «Webhook Verificación Meta»
   - HTTP Method: **GET**
   - Path: **`whatsapp-inbound`** (el mismo que el POST)
   - Respond: **Using 'Respond to Webhook' Node**
2. **IF** «¿Token Meta válido?» — las dos condiciones (AND):
   - `{{ $json.query['hub.mode'] }}` es igual a `subscribe`
   - `{{ $json.query['hub.verify_token'] }}` es igual a `<TOKEN DE X3>`
3. Rama **true** → **Respond to Webhook** «Responder Challenge»:
   Respond With **Text**, cuerpo `{{ $json.query['hub.challenge'] }}`, código
   **200**.
4. Rama **false** → **Respond to Webhook** «Rechazar Verificación»: código
   **403**.

El token va escrito en el IF de momento. Es poco sensible (solo sirve para
verificar la URL), pero **no se commitea en ningún sitio**. Pendiente para
cuando haya varias clínicas: Meta no dice en esa llamada de qué número viene,
así que n8n no puede buscar el token de cada clínica.

Si n8n se queja de que el path ya está en uso, **para y avisa**: GET y POST en el
mismo path deberían poder convivir, y la URL tiene que ser la misma para los
dos.

**Comprobación antes de avisar a Xexu** (publicado el cambio):

```bash
# Debe devolver exactamente: 12345
curl "https://n8n.alt4ir.online/webhook/whatsapp-inbound?hub.mode=subscribe&hub.verify_token=<TOKEN>&hub.challenge=12345"

# Con un token equivocado debe dar 403
curl -i "https://n8n.alt4ir.online/webhook/whatsapp-inbound?hub.mode=subscribe&hub.verify_token=mal&hub.challenge=12345"
```

→ **🔗 B:** avisa a Xexu.

### 👤 X4 · Verificar la URL en Meta (Xexu) — M5, primera mitad

En la app **AutoClinic Gaena** → caso de uso de WhatsApp → apartado
**Webhooks** (en la guía de Meta aparece en el Paso 2):

- **URL de devolución de llamada:** `https://n8n.alt4ir.online/webhook/whatsapp-inbound`
- **Token de verificación:** el de X3
- **Verificar y guardar.**

Comprobarlo en n8n (Executions): aparece una ejecución que empieza en `Webhook
Verificación Meta`. Si acaba en `Responder Challenge`, bien; si acaba en
`Rechazar Verificación`, el token no coincide (espacios al copiar, o distinto en
Meta y en n8n).

**Todavía no suscribirse a `messages`**: se hace en X5, cuando n8n ya sepa
contestar. Si nos suscribimos antes, cada mensaje recorre un flujo a medias y
deja errores.

### ✅ 🛠️ J3 · Parar los avisos de estado (Jesús)

> **Hecho**, con el IF llamado **`¿Es aviso de estado?`** en vez de «¿Es un
> mensaje?» (con `skip === true` → rama true, el nombre del plan decía lo
> contrario de lo que hace). Comprobado con un POST de aviso simulado: la
> ejecución termina en ese IF y no llega a `Cargar Config Clínica`.

Por cada respuesta que mande el agente, Meta enviará 2-3 avisos más (enviado,
entregado, leído). `Normalizar Mensaje` los marca con `skip: true`, pero hoy el
flujo sigue igualmente hasta `Cargar Config Clínica` y acaba en error cada vez.

Entre `Normalizar Mensaje` y `Cargar Config Clínica`, un **IF** «¿Es un
mensaje?»:

- Condición booleana: `{{ $json.skip === true }}` es **true**. Con
  `=== true` y no `$json.skip` a secas, porque en los mensajes normales `skip`
  no existe y la validación estricta del IF fallaría con un `undefined`.
- Rama **true** → sin conexión (el flujo termina).
- Rama **false** → `Cargar Config Clínica`.

Más adelante esos avisos se mandarán a Django para los ✓✓ (N5).

### ✅ 🛠️ J4 · Quitar `Responder a Webhook Postman` (Jesús) — N2

> **Hecho.**

Conecta `¿Es Test?` (salida **false**) directamente a `Enviar Mensaje
WhatsApp` y borra `Responder a Webhook Postman`. Con el webhook respondiendo
«Immediately», ese nodo intenta responder a una petición que ya se respondió, y
puede cortar el flujo justo antes de enviar.

`Webhook Test` y `Responder Webhook Test` **no se tocan**: los usa el chat de
prueba del panel.

### ✅ 🛠️ J5 · Enviar en el formato de Meta (Jesús) — N1

> **Hecho**, opcional N4 incluido: `Registrar Mensaje Saliente` y `Registrar
> Respuesta Solo Texto` toman primero `messages[0].id` de la respuesta de Meta
> (y siguen entendiendo el formato WaAPI). El texto va como
> `JSON.stringify(String(... || ''))` para que un mensaje vacío no rompa el JSON.
> Sin probar todavía contra Meta: se verá en la prueba conjunta.

**`Enviar Mensaje WhatsApp`** (HTTP Request):

- Método: **POST**
- URL: `https://graph.facebook.com/v26.0/{{ $('Preparar Contexto').first().json.whatsapp_phone_number_id }}/messages`
- Cabecera: `Authorization` = `Bearer {{ $('Preparar Contexto').first().json.whatsapp_token }}`
- Body (JSON):

```
{
  "messaging_product": "whatsapp",
  "to": "{{ $('Normalizar Mensaje').first().json.phone }}",
  "type": "text",
  "text": {
    "preview_url": false,
    "body": {{ JSON.stringify($('Formatear respuesta').first().json.message) }}
  }
}
```

- **El `JSON.stringify` es obligatorio:** las respuestas del agente llevan saltos
  de línea y comillas, y sin él el JSON se rompe.
- `to` va sin `+` (Meta lo manda así en `msg.from` y así lo espera).
- Este nodo tiene `continueOnFail`: si falla, **no da error visible**. En las
  pruebas, mira siempre su salida.

**`Responder Solo Texto`** (la respuesta fija a fotos y audios): mismo cambio de
URL, cabecera y formato, con el texto fijo en `body`.

**Opcional, 2 minutos (N4):** en `Registrar Mensaje Saliente`, añade
`wa_message_id` = `{{ $('Enviar Mensaje WhatsApp').first().json.messages?.[0]?.id }}`.
Deja preparado el ✓✓ de las respuestas del agente.

**Estos nodos solo están en el camino del WhatsApp real.** El chat de prueba del
panel no pasa por ellos.

### ✅ 🛠️ J6 · Publicar y comprobar que no se ha roto nada (Jesús)

> **Hecho.** Publicado y activo; el chat de prueba del panel contesta como
> siempre (ejecución 3085).

1. Guarda y **publica** el workflow.
2. **Chat de prueba del panel** (Agente → Chat): un «Hola». Tiene que contestar
   como siempre. Si no, algo del camino común se ha tocado sin querer.

→ **🔗 C:** avisa a Xexu.

### 👤 X5 · Suscribirse a los mensajes (Xexu) — M5, segunda mitad

En el mismo apartado de **Webhooks** de la app: suscribirse al campo
**`messages`**.

---

## 5. Prueba conjunta (guion)

Jesús escribe desde su móvil al **+1 555 156 9032**; Xexu mira n8n y AutoClinic.
Luego se cambian si hace falta. Para la batería completa de preguntas, ver
`preguntas-tipo-agentetest/preguntastest.md`; esto es lo que interesa probar
hoy **en modo real**.

| # | Mensaje | Qué debe pasar | Dónde mirar |
|---|---|---|---|
| 1 | «Hola» | Lo trata como paciente nuevo y pide nombre, apellidos y correo | Móvil · n8n · **Chats de Gaena** (el hilo aparece en vivo) |
| 2 | Datos: «PRUEBA Jesús …» + correo | Resume y pide confirmación antes de darlo de alta | Pacientes de Gaena |
| 3 | «¿Dónde estáis?» | Contesta con la base de conocimiento de Gaena | Móvil |
| 4 | «¿Cuánto cuesta una quiropodia?» | Precio literal del catálogo (si es un rango, el rango) | Móvil |
| 5 | «¿Qué horario tiene la podóloga?» | Horario semanal de la profesional | Móvil |
| 6 | Pedir cita a 2-3 semanas vista | Propone huecos reales, repite la fecha con día de la semana y pide confirmación | Agenda de Gaena |
| 7 | «Quiero cancelar mi cita» → «sí» | Enseña la cita, pide confirmación y la cancela | Agenda de Gaena |
| 8 | Muy seguido: «hola» / «queria» / «cita para el jueves» | **Una sola respuesta**, a los tres a la vez (debounce) | Móvil · n8n (dos ejecuciones cortadas, una completa) |
| 9 | Xexu escribe desde **Chats** de Gaena en el hilo de Jesús | Le llega a Jesús desde el +1 555. Si Jesús contesta, el agente **no** responde durante 5 minutos | Móvil · Chats |
| 10 | Xexu pone el hilo en **Modo humano** | El agente deja de contestar hasta que se quite | Chats |
| 11 | Una foto | Respuesta fija de «solo texto» (lo de las fotos por WhatsApp real es N8, pendiente) | Móvil |
| 12 | «Me corté un callo con una cuchilla y no para de sangrar» | Frase que Azure bloqueó en las pruebas. Ver **qué recibe el paciente** en modo real | Móvil · n8n (`Clasificar Error Agente`) |

**El 9 prueba también Django:** el mensaje del panel sale directamente por la API
de Meta desde Django, que usa la versión **v21.0** por defecto (D5). Si falla con
un error de versión, se arregla definiendo `WHATSAPP_GRAPH_API_VERSION` en los
settings (`config/settings/base.py`) leyendo del `.env`, con `v26.0`.

Apuntad los resultados en la tabla del final.

---

## 6. Qué no se prueba hoy

- **Plantillas y recordatorios:** no están aprobadas, y el flujo aún no envía
  plantillas (M3, N7, D1).
- **✓✓ de entrega:** los avisos de estado se descartan en J3 (N5).
- **Fotos y audios guardados:** `WA-Media-Ingest` sin enganchar (N6, N8).
- **Transcripción de audios** (T).
- **Nombre de la clínica en el chat:** sale el número; hace falta nombre
  aprobado y, en la práctica, verificación (M2).
- **Escribir fuera de la ventana de 24 h** desde el panel (D1, Fase 3).

---

## 7. Si algo falla

| Síntoma | Causa probable | Qué mirar |
|---|---|---|
| La ejecución falla en `Cargar Config Clínica` con **403** «no tiene permiso» | La clave maestra no coincide entre Django (`AGENT_MASTER_API_KEY` del `.env`) y la credencial de n8n **`Django Agent Master Key`** (Header Auth: Name `Authorization`, Value `Api-Key <clave>`) | **Pasó el 29/09 y se arregló igualando las dos.** El loader nunca se había usado: el chat de prueba no pasa por él. n8n no la lee de su entorno (se puede quitar de ahí). Para distinguirlo de un 404: con la clave bien, un `phone_number_id` desconocido da **404**, no 403 |
| Meta: «no se pudo validar la URL» | El GET no responde bien | El `curl` de J2; que el workflow esté publicado; mismo token en los tres sitios |
| Escribo y no aparece ninguna ejecución en n8n | No suscritos a `messages`, o la WABA no está suscrita a la app | X5. Si está, Xexu revisa en Meta que la app esté suscrita a la WABA `799132616628033` |
| La ejecución falla en `Cargar Config Clínica` | Ninguna clínica con ese Phone Number ID, o dos | X2 |
| El agente contesta en n8n pero no llega al móvil | Fallo en `Enviar Mensaje WhatsApp` (no da error visible) | Su salida: `401` → token caducado o mal copiado. `(#131030)` → el destinatario no está en la lista de verificados. `400` → formato del JSON |
| Salen errores a cada mensaje enviado | Los avisos de estado recorren el flujo | J3 |
| El chat de prueba del panel dejó de contestar | Se tocó algo del camino común | Volver a la copia de J0 y comparar |

---

## 8. Al terminar

- Cancelar las citas de prueba que queden y comprobarlo en la agenda.
- Decidir con Elena qué se hace con los pacientes «PRUEBA» y con las
  conversaciones de prueba. Las conversaciones solo se pueden borrar enteras,
  desde el admin.
- Reactivar el flujo de recordatorios si se pausó en J1.
- Reactivar `WA-Post-Visit-Followup` (desactivado el 29/09: escribe a diario a
  las 10:00 a los pacientes con cita el día anterior). Ojo: mientras Gaena
  apunte al número de prueba, intentaría escribir desde él a pacientes reales, y
  además su envío sigue en formato WaAPI. Mejor dejarlo parado hasta M7 o
  hasta pasarlo al formato de Meta.
- 👤 **X6 (Xexu) · Token permanente (M4).** Business Manager de Gaena →
  Usuarios del sistema → crear uno administrador → asignarle la app
  *AutoClinic Gaena* y la WABA `799132616628033` → generar token **sin
  caducidad** con `whatsapp_business_messaging` y
  `whatsapp_business_management` → sustituir el temporal en AutoClinic (X2).
- Gaena se queda apuntando al número de prueba hasta la migración de su número
  real (M7). Ese día se cambian el Phone Number ID y el token.
- Actualizar `PENDIENTE-PRODUCCION.md`: N1, N2, N3, N4 (hecho en J5) y M5.

---

## 9. Resultados

| # | ¿Bien? | Qué pasó / notas |
|---|---|---|
| 1 | ✅ | Onboarding de paciente nuevo por WhatsApp real (30/09) |
| 2 | ✅ | Alta del paciente completada |
| 3 | ✅ | |
| 4 | ✅ | |
| 5 | ✅ | |
| 6 | ✅ | Cita creada desde WhatsApp |
| 7 | ✅ | Listar citas y cancelar, funcionan |
| 8 | ✅ | |
| 9 | ✅ | Respuesta humana desde el panel llega al móvil |
| 10 | ✅ | |
| 11 | ⬜ | Pendiente: conectar las imágenes (`WA-Media-Ingest`, N6/N8) |
| 12 | ✅ | |
