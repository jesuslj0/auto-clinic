# Prompt del agente de n8n — registro del historial de chat

Este documento contiene el bloque que hay que **añadir** al prompt del nodo AI Agent de
n8n y el contrato exacto de la API que debe usar a través del MCP.

> ⚠️ **Esto NO sustituye el prompt actual del agente.** El agente de n8n ya tiene su
> personalidad, su tono y sus reglas de negocio (citas, horarios, servicios). El bloque
> de abajo se **pega al final** de ese prompt, sin borrar nada. Solo añade una
> responsabilidad nueva —registrar lo que se dice— y no toca cómo conversa ni cómo
> gestiona citas.
>
> Antes de tocar nada, usa el [prompt de revisión previa](#revisión-previa-de-la-instancia-n8n):
> hace que Claude inspeccione la instancia por MCP y te informe **antes** de modificar
> ningún workflow.

---

## Bloque a añadir al final del prompt del agente

```text
## Registro del historial de conversación

Además de tus tareas actuales, eres responsable de dejar registrado en el panel de la
clínica TODO lo que se dice en la conversación, usando la herramienta de mensajes de la
API de Auto Clinic expuesta por el MCP. Esto no cambia en nada cómo atiendes al paciente
ni cómo gestionas sus citas: es una tarea añadida.

### Regla principal

Por cada turno de conversación registras SIEMPRE dos mensajes, en este orden:

1. El mensaje que acaba de enviar el paciente (ANTES de pensar tu respuesta).
2. La respuesta que tú envías (INMEDIATAMENTE DESPUÉS de enviarla por WhatsApp).

Registrar el mensaje entrante antes de responder no es opcional: si tu respuesta falla,
el staff tiene que poder ver igualmente lo que preguntó el paciente.

### Herramienta: crear mensaje de chat

POST /api/agent/messages/

Campos:

| Campo          | Obligatorio | Valor                                                        |
|----------------|-------------|--------------------------------------------------------------|
| phone          | sí*         | Teléfono del PACIENTE en E.164, p. ej. "+34600111222"        |
| direction      | sí          | "inbound" (lo escribe el paciente) o "outbound" (lo envías tú)|
| sender         | sí          | "patient", "agent" o "staff"                                  |
| body           | sí*         | Texto literal del mensaje                                     |
| message_type   | no          | "text" (por defecto), "image", "audio", "video", "document", "location", "template", "other" |
| media_url      | no          | URL del adjunto si el mensaje no es de texto                  |
| media_mime     | no          | Tipo MIME del adjunto, p. ej. "image/jpeg"                    |
| wa_message_id  | recomendado | ID del mensaje en la WhatsApp Cloud API, p. ej. "wamid.HBgL..."|
| status         | no          | Solo en salientes: "sent", "delivered", "read" o "failed"     |
| sent_at        | recomendado | Marca de tiempo del mensaje en ISO 8601 UTC                   |
| raw            | no          | Payload original de Meta, tal cual, sin modificar             |

(*) `phone` es obligatorio salvo que indiques `session` con el id de una conversación
ya existente. `body` puede ir vacío solo si mandas `media_url`.

Combinaciones válidas de direction/sender:
- Paciente escribe  → direction "inbound",  sender "patient"
- Tú respondes      → direction "outbound", sender "agent"
- Responde un humano→ direction "outbound", sender "staff"

Nunca uses "inbound" con sender "agent", ni "outbound" con sender "patient".

### Reglas estrictas

1. Transcribe el mensaje LITERAL. No resumas, no corrijas faltas, no traduzcas y no
   añadas comentarios propios al campo `body`.
2. Usa siempre el teléfono del PACIENTE en `phone`, nunca el número de la clínica, ni
   en los mensajes salientes. `phone` identifica la conversación, no al emisor.
3. Incluye `wa_message_id` siempre que lo tengas. La API es idempotente por ese campo:
   si reintentas, responde 201 con el mensaje ya guardado en vez de duplicarlo. Por eso
   NO debes comprobar antes si un mensaje existe: envíalo directamente.
4. Si una llamada devuelve 4xx, NO la repitas con los mismos datos. Registra el fallo en
   POST /api/agent/errors/ con {workflow, node_name, error_message, phone} y sigue
   atendiendo al paciente: un error de registro no debe romper la conversación.
5. No inventes ningún valor. Si no tienes `wa_message_id` o `sent_at`, omítelos; es
   preferible a rellenarlos con datos falsos.
6. No uses /api/agent/memory/ para guardar la conversación. Esa tabla es tu memoria de
   contexto y se trunca; el historial que ve la clínica es /api/agent/messages/.
7. No intentes editar ni borrar mensajes: el historial es de solo escritura y
   PATCH/DELETE devuelven 405.
8. No hace falta crear la conversación: si el teléfono es nuevo, la API la crea sola y
   la vincula con la ficha del paciente si el número coincide.
9. Antes de contestar puedes consultar GET /api/agent/messages/?session=<id> para releer
   el hilo, pero solo si necesitas contexto que no esté ya en tu memoria.

### Ejemplos

Mensaje entrante del paciente:
{
  "phone": "+34600111222",
  "direction": "inbound",
  "sender": "patient",
  "body": "Hola, quería cambiar mi cita del jueves",
  "message_type": "text",
  "wa_message_id": "wamid.HBgLMzQ2MDAxMTEyMjIVAgASGBQz",
  "sent_at": "2026-07-21T09:14:02Z"
}

Tu respuesta:
{
  "phone": "+34600111222",
  "direction": "outbound",
  "sender": "agent",
  "body": "Claro. Tengo hueco el viernes a las 10:00 o a las 12:30, ¿cuál prefieres?",
  "message_type": "text",
  "wa_message_id": "wamid.HBgLMzQ2MDAxMTEyMjIVAgARGBI5",
  "status": "sent",
  "sent_at": "2026-07-21T09:14:05Z"
}

Audio que envía el paciente:
{
  "phone": "+34600111222",
  "direction": "inbound",
  "sender": "patient",
  "body": "",
  "message_type": "audio",
  "media_url": "https://.../media/abc123.ogg",
  "media_mime": "audio/ogg",
  "wa_message_id": "wamid.HBgLMzQ2MDAxMTEyMjIVAgASGBQ0",
  "sent_at": "2026-07-21T09:15:30Z"
}
```

---

## Revisión previa de la instancia n8n

Pásale esto a Claude **en una sesión que tenga el MCP de n8n conectado**. Está diseñado
para que inspeccione primero y no toque nada hasta que tú lo autorices.

```text
Tienes conectado el MCP de la instancia de n8n. Vamos a añadir el registro del historial
de chat al agente de WhatsApp, pero el workflow está EN PRODUCCIÓN y no quiero romperlo.

## FASE 1 — Solo lectura. No modifiques nada todavía.

Prohibido en esta fase: crear, actualizar, activar, desactivar o borrar workflows, nodos
o credenciales. Solo herramientas de lectura.

Averigua y repórtame:

1. Qué workflows existen y cuál atiende los mensajes entrantes de WhatsApp. Si hay más de
   un candidato (producción, test, versiones antiguas), lístalos y NO elijas por tu cuenta.
2. En ese workflow: qué nodos lo componen y en qué orden, en particular el webhook de
   entrada, el nodo AI Agent y el nodo que envía la respuesta por la WhatsApp Cloud API.
3. El system prompt actual del nodo AI Agent, ÍNTEGRO y literal. No lo resumas: quiero
   verlo tal cual para saber qué se conserva.
4. Qué herramientas tiene disponibles el agente (nodos tool y, si hay un MCP Client,
   qué tools expone). Dime explícitamente si entre ellas está la de crear mensajes de
   chat de Auto Clinic (POST /api/agent/messages/). Si NO está, dímelo antes que nada:
   sin esa herramienta el cambio de prompt no sirve de nada.
5. Si ya hay algún nodo que escriba en /api/agent/messages/ o en /api/agent/memory/, para
   no duplicar el registro.
6. Cómo está configurada la autenticación hacia la API de Auto Clinic (nombre de la
   credencial y si usa la cabecera `Api-Key`). No me muestres el valor del secreto.

Cuando lo tengas, dame un informe corto y espera mi confirmación.

## FASE 2 — Propuesta. Sigue sin modificar nada.

Con lo anterior, propón el cambio mínimo:

- El bloque nuevo se AÑADE AL FINAL del system prompt actual. El prompt existente se
  conserva palabra por palabra: no lo reescribas, no lo reordenes, no lo "mejores".
- Muéstrame un diff claro: qué había antes y qué queda después.
- Si detectas que un nodo HTTP ya registra el mensaje entrante, avísame: en ese caso el
  bloque debe pedir SOLO el registro del saliente, o el hilo saldrá duplicado.
- No cambies el modelo, la temperatura, las credenciales ni ningún otro nodo.

Espera mi aprobación explícita.

## FASE 3 — Aplicar.

Solo cuando yo lo apruebe:

1. Antes de escribir, exporta el JSON del workflow tal como está y guárdalo como copia
   de seguridad. Dime dónde lo has dejado.
2. Aplica ÚNICAMENTE el cambio aprobado en el system prompt del nodo AI Agent.
3. No actives ni desactives el workflow; déjalo en el estado en que estaba.
4. Vuelve a leer el workflow y confírmame que el prompt anterior sigue intacto y que el
   bloque nuevo está al final.
5. Dime cómo revertirlo con el JSON exportado.

Si en cualquier momento algo no cuadra —hay varios workflows candidatos, el prompt no
aparece donde esperabas, falta la herramienta de mensajes— PARA y pregunta. Prefiero
responder una pregunta a deshacer un cambio en producción.
```

> Nota: el MCP de n8n no está conectado en esta sesión de trabajo del repositorio, así que
> este prompt hay que ejecutarlo donde sí lo esté.

---

## Configuración de la herramienta en n8n

- **URL base**: la de tu Django (`https://<tu-dominio>`), no la de n8n.
- **Cabecera de autenticación**: `Authorization: Api-Key <agent_api_key de la clínica>`.
  La clave está en el panel, en *Agente WhatsApp* → se puede rotar desde ahí.
- La clave **determina la clínica**. No mandes `clinic` en el cuerpo: aunque lo envíes,
  el servidor lo ignora e impone la clínica de la clave. Un agente no puede escribir en
  el hilo de otra clínica.

### Respuestas que puede devolver

| Código | Significado | Qué debe hacer el agente |
|--------|-------------|--------------------------|
| 201 | Mensaje registrado (o ya existía, si repetiste `wa_message_id`) | Continuar |
| 400 | Payload inválido: falta `phone`/`session`, o `body` y `media_url` ambos vacíos | No reintentar con los mismos datos |
| 401/403 | `Api-Key` ausente, inválida o revocada | No reintentar; revisar la clave en el panel |
| 405 | Se intentó `PATCH` sobre un mensaje | No hacerlo: el historial es append-only |
| 403 | Se intentó `DELETE` (el agente no tiene permiso de borrado) | No hacerlo |

---

## Arquitectura del workflow (decidida)

**El registro del historial no depende del criterio del LLM.** Un modelo puede decidir que
un mensaje "no merece" registrarse, o encadenar dos respuestas y guardar solo una. La
integridad la garantizan dos nodos HTTP Request deterministas:

```
[Webhook Meta]
      │
      ▼
[HTTP Request] ──► POST /api/agent/messages/   direction=inbound, sender=patient
      │            (registra ANTES de que el agente piense)
      ▼
[AI Agent] ─────► solo conversa; usa el MCP para citas, pacientes, etc.
      │
      ▼
[WhatsApp Cloud API] ──► envía la respuesta y devuelve wa_message_id
      │
      ▼
[HTTP Request] ──► POST /api/agent/messages/   direction=outbound, sender=agent,
                   status=sent, wa_message_id=<el que devolvió Meta>
```

Puntos a cuidar al montarlo:

- **El primer nodo va antes del agente, no en paralelo.** Si la IA falla o agota el
  tiempo, el mensaje del paciente ya está en el panel.
- **El `wa_message_id` del saliente lo devuelve la Cloud API**, no Meta en el webhook:
  por eso el segundo nodo va *después* del envío. Si el envío falla, registra igualmente
  el mensaje con `status: "failed"` y `error_message`.
- **Ambos nodos usan la misma cabecera** `Authorization: Api-Key <agent_api_key>`.
- Como la API es idempotente por `wa_message_id`, activar "Retry on Fail" en estos nodos
  es seguro: un reintento no duplica el mensaje.

El prompt de arriba sigue sirviendo como red de seguridad, para el caso de que el agente
actúe fuera del flujo principal.
