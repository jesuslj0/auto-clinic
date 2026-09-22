# Plan: dejar el apartado de Chats listo para producción

**Contexto y por qué esto es urgente.** Vamos a conectar el número de la clínica
Gaena a la WhatsApp Cloud API por la vía clásica (no Coexistence, porque
requiere que Propus esté verificada en Meta como Tech Provider y esa
verificación está bloqueada). La vía clásica tiene una consecuencia que manda
sobre todo lo demás:

> **El día que migremos el número, la clínica pierde la app de WhatsApp para ese
> número.** El apartado de Chats de AutoClinic pasa a ser su *única* forma de
> hablar con sus pacientes. No hay plan B ni segunda pantalla.

Eso cambia el listón: lo que hoy es "un panel de lectura que ya iremos
mejorando" tiene que ser, el minuto 0, un cliente de mensajería completo. Este
documento es lo que hay que construir para llegar ahí, en orden.

Elena (la podóloga de Gaena) trabaja sola. Va a tener AutoClinic abierto entre
paciente y paciente, muchas veces desde el móvil. Ese es el usuario para el que
hay que diseñar cada decisión de este plan.

---

## Qué hay ya, y qué no

Conviene empezar sabiendo que la base está bien y que esto es sobre todo
completar, no rehacer.

**Ya funciona:**

- `ChatMessage` es append-only y con `wa_message_id` único, así que los reintentos
  de Meta no duplican el hilo (`agent/models.py`).
- `record_message()` es el embudo único de escritura, idempotente, y mantiene la
  cabecera denormalizada de la sesión (`agent/services.py`).
- Las dos pausas del agente (`agent_paused` explícita y la temporal por
  `last_staff_message_at`) y el endpoint `should-reply` que consulta n8n.
- El envío del staff va directo a la Cloud API, sin pasar por n8n, así que la
  clínica puede escribir aunque el bot esté caído (`agent/whatsapp.py`).
- La ventana de 24 h está modelada (`can_send_free_text`) y el compositor se
  bloquea cuando expira.
- nginx ya proxea `/ws/` con `Upgrade` y `proxy_read_timeout 86400`. Por ahí no
  hay trabajo.

**Lo que falta, y es justo lo que la vía clásica convierte en bloqueante:**

| Hueco | Por qué bloquea |
|---|---|
| No hay tiempo real | Hay que recargar para ver un mensaje nuevo. Inaceptable como único canal. |
| Los adjuntos se rompen | `media_url` guarda una URL de Meta, que caduca y exige token. En podología los pacientes mandan fotos constantemente. |
| No se puede escribir fuera de las 24 h | Sin la app del móvil, la clínica **no tiene ninguna forma** de contactar a un paciente pasado ese plazo. |
| No hay estados de entrega | Sin la app, "¿le habrá llegado?" no tiene respuesta. |

Y una deuda de seguridad que arrastramos y que no hay que replicar:

- `AppointmentConsumer` (`appointments/consumers.py`) **acepta cualquier
  conexión**: no mira `scope['user']` ni comprueba la clínica. Cualquiera que
  sepa un `clinic_id` recibe las citas de esa clínica.
- `appointments/routing.py` usa `(?P<clinic_id>\d+)`, pero `Clinic.clinic_id` es
  un `CharField`. Con un id no numérico esa ruta no casa nunca, así que además
  está muerta.

---

## Orden de trabajo

Cuatro fases. Están ordenadas por dependencia, no por importancia: la fase 1
crea la infraestructura (y la separación de plantillas) de la que se cuelgan las
demás.

1. **Tiempo real** — consumer, eventos y troceado de plantillas.
2. **Adjuntos** — descarga, bucket privado y servido firmado.
3. **Plantillas fuera de la ventana de 24 h.**
4. **Estados de entrega y badge de no leídos.**

Las fases 1 a 3 son bloqueantes para conectar el número. La 4 se puede cerrar la
semana siguiente si hiciera falta, pero es barata una vez estén las otras.

---

## Fase 1 — Tiempo real

### 1.1 Consumer de chats

Archivo nuevo `agent/consumers.py`, con `ChatConsumer(AsyncWebsocketConsumer)`.

**Decisión de diseño: la clínica NO va en la URL.** La ruta es `ws/chats/`, a
secas, y la clínica se saca de `scope['user'].clinic_id`. Así el aislamiento
multi-tenant no depende de lo que escriba el cliente, y no repetimos el agujero
de `AppointmentConsumer`.

En `connect()`:

- Si `scope['user']` es anónimo o inactivo → `close(code=4401)`.
- Si el usuario no tiene clínica (equipo de plataforma) → `close(code=4403)`.
  Ese caso no tiene bandeja que escuchar.
- Si la tiene → `group_add(group_name_for(clinic_id))` y `accept()`.

**Ojo con el nombre del grupo.** Channels exige que case con
`[a-zA-Z0-9_.-]{1,100}`, y `Clinic.clinic_id` es un `CharField(max_length=100)`
que elegimos nosotros: puede traer caracteres inválidos y, con el prefijo,
pasarse de 100. Hay que derivarlo, no concatenarlo:

```python
def group_name_for(clinic_id: str) -> str:
    digest = hashlib.blake2s(clinic_id.encode(), digest_size=8).hexdigest()
    return f'chats.{digest}'
```

**El consumer es de solo lectura.** No acepta mensajes del cliente al servidor:
enviar sigue siendo un POST HTTP normal. Eso nos ahorra resolver autenticación,
CSRF y validación otra vez dentro del socket, y deja un único camino de
escritura (`record_message`). Si llega algo del cliente, se ignora.

Métodos manejadores: `chat_message` y `chat_session`.

### 1.2 De paso, cerrar el agujero de citas

En el mismo PR, `AppointmentConsumer` pasa a sacar la clínica del usuario
autenticado igual que el de chats, y `appointments/routing.py` deja de usar
`\d+`. Es poco trabajo y hoy es una fuga de datos de una clínica a otra.

### 1.3 Emisión de eventos

Archivo nuevo `agent/realtime.py` con `broadcast_message(message)` y
`broadcast_session(session)`.

**Decisión de diseño: se emite desde `record_message()`, no desde una señal
`post_save`.** `record_message` es el embudo por el que pasan la ingesta de n8n
y el envío del staff, y es donde se actualiza la cabecera de la sesión. Una
señal dispararía *antes* de esa actualización y dentro de la transacción.

Y se emite con `transaction.on_commit(...)`, para que el consumer nunca avise de
una fila que todavía no está confirmada.

Puntos de emisión:

| Dónde | Evento |
|---|---|
| `record_message()` | `chat_message` + `chat_session` |
| `mark_session_read()` | `chat_session` |
| `ChatToggleAgentView` | `chat_session` |
| `ClinicAgentSwitchView` | `chat_session` para todos los hilos afectados |
| Endpoint de estados (fase 4) | `chat_message` |

**Payload mínimo, a propósito:**

```json
{"type": "message", "session_id": "...", "message_id": "...",
 "direction": "inbound", "unread_count": 3, "last_message_at": "..."}
```

No mandamos el cuerpo del mensaje por el socket. El navegador, al recibir el
aviso, pide el fragmento HTML renderizado. Así el HTML lo sigue generando Django
con sus tokens de tema, no duplicamos el markup de la burbuja en JavaScript, y
no metemos texto clínico en más capas de las necesarias.

### 1.4 Trocear la plantilla

`templates/agent/chat_inbox.html` son 312 líneas en un archivo. **Este es el 80 %
del trabajo de la fase**, y sin él no hay actualización parcial posible.

Partir en:

- `agent/_session_list.html` — el contenedor de la lista
- `agent/_session_row.html` — una fila
- `agent/_thread_header.html` — cabecera del hilo, con el conmutador de modo
- `agent/_message_bubble.html` — una burbuja
- `agent/_composer.html` — el pie (compositor o aviso de ventana cerrada)

Cada fragmento tiene que poder renderizarse aislado, así que lo que hoy sale del
contexto global (`query`, `request.user.clinic`) hay que pasarlo explícito.

La burbuja lleva `data-message-id="{{ message.id }}"`, que es lo que usa el
cliente para deduplicar.

### 1.5 Vistas de fragmento

Dos vistas nuevas, ambas con la misma comprobación de clínica que ya hace
`ChatSessionActionMixin`:

- `GET /chats/<session_id>/mensajes/?after=<message_id>` → las burbujas
  posteriores a ese id, en orden cronológico. Sin `after`, las últimas 50.
- `GET /chats/lista/?q=&unread=` → la lista de conversaciones, respetando los
  filtros actuales.

**El parámetro `after` es la pieza clave de robustez del diseño.** El WebSocket
no garantiza entrega: se cae el wifi, se suspende el móvil, se reinicia Daphne.
Como el cliente siempre pide "lo que hay después del último id que tengo", al
reconectar se cura solo sin lógica adicional. Un evento perdido no deja un hueco
permanente en la conversación.

### 1.6 Cliente

Componente Alpine en `chat_inbox.html`:

- Abre el socket a `ws/chats/`.
- Evento sobre la sesión abierta → `fetch` de `mensajes/?after=<último id en el
  DOM>` y añadir al final. Descartar los que ya tengan ese `data-message-id`.
- Evento sobre otra sesión → refrescar la lista y el badge.
- Auto-scroll **solo si el usuario ya estaba abajo**. Si está leyendo hacia
  arriba, no moverle la vista; mostrar un "nuevos mensajes ↓".
- Reconexión con backoff exponencial y tope (1s, 2s, 4s… hasta 30s). Al
  reconectar, `fetch` con `after` para recuperar lo perdido.
- **Fallback:** si el socket no conecta tras 2-3 intentos, hacer polling del
  mismo endpoint cada 15 s y avisar discretamente en la interfaz. Un proxy o una
  red móvil que corte WebSockets no puede dejar a la clínica sin mensajes.

### Criterios de aceptación de la fase 1

- Dos navegadores abiertos: un mensaje entrante por la API aparece en menos de
  2 s en ambos, sin recargar.
- Estando en otra sección (Agenda), el contador de no leídos se actualiza.
- Cortando la red 30 s y volviendo, los mensajes de ese hueco aparecen al
  reconectar.
- Un usuario de la clínica A no recibe absolutamente nada de la clínica B.

### Tests (`tests/agent/test_chat_realtime.py`)

Con `channels.testing.WebsocketCommunicator`:

- Conexión anónima rechazada.
- Usuario sin clínica rechazado.
- Usuario de la clínica A no recibe el evento de la clínica B.
- `record_message()` emite **después** del commit (comprobar que dentro de un
  `atomic` no ha salido todavía).
- Las vistas de fragmento devuelven 404 para una sesión de otra clínica.

---

## Fase 2 — Adjuntos

### El problema

`ChatMessage.media_url` es un `URLField`. Las URLs de media de Meta caducan y
exigen el token para descargarlas: la foto se ve un rato y luego queda rota
para siempre. Hoy no se nota porque no hay tráfico real; el día 1 con Gaena, sí.

### Cómo se guardan

Una foto de un pie enviada por un paciente **es dato clínico**. Se le aplican
las reglas que ya están escritas en `CLAUDE.md` y que implementa
`clinical/files.py`:

- Bucket privado `clinical_media`, **nunca** `MEDIA_URL`.
- Prefijo propio `chat-media/`, separado de `lesion-attachments/` y
  `consent-signatures/`: son documentos con ciclo de vida distinto y así se
  pueden aplicar políticas de bucket distintas sin mover objetos.
- Clave UUID, nunca nombre derivado del paciente.
- Se guarda **la clave del objeto, nunca una URL**. Las URL se firman por
  petición y caducan.

Campo nuevo en `ChatMessage`:

```python
media_file = models.FileField(
    storage=clinical_media_storage(), upload_to=chat_media_upload_to, blank=True
)
```

`media_url` se queda donde está por compatibilidad, pero deja de usarse para
entrantes.

### Cómo entran

n8n recibe el `media_id` del webhook, lo resuelve contra Graph, descarga el
binario y lo manda a un endpoint nuevo, multipart:

`POST /api/agent/messages/<id>/media/`

**Decisión: n8n descarga y nos manda el binario**, en vez de mandarnos el
`media_id` para que lo descarguemos nosotros. Así el token de WhatsApp de la
clínica no tiene que salir de donde ya está y Django no hace peticiones
salientes a Graph dentro del ciclo de una request.

La validación es **por contenido**, igual que los adjuntos clínicos:

- **Imágenes:** reutilizar `validate_clinical_image(file, external=True)` tal
  cual. `external=True` es exactamente este caso (origen no profesional, límite
  de tamaño más estricto).
- **Audio:** hace falta un camino nuevo. Archivo nuevo `agent/files.py`,
  espejo de `clinical/files.py` pero para audio: lista blanca de `audio/ogg`
  (que es lo que manda WhatsApp para las notas de voz) y `audio/mpeg`,
  validación por firma de los primeros bytes, sin Pillow. **No tocar
  `clinical/files.py` para meter audio ahí**: son reglas distintas para
  documentos distintos.

**Por qué el audio entra en el alcance y no se pospone:** el agente no entiende
notas de voz (limitación conocida). Si Elena tampoco puede oírlas porque no
tiene la app, un paciente mayor que manda una nota de voz se queda sin respuesta
de nadie. Es el caso que más fácil se nos escapa y el más grave.

- **Vídeo y documentos:** fuera de alcance en v1. En el hilo se pinta un aviso
  claro de tipo "📎 Documento no disponible en el panel", nunca un enlace roto.

### Cómo se sirven

Vista nueva `GET /chats/media/<message_id>/`:

- Solo sesión de staff de esa clínica. **Denegada explícitamente al token del
  agente**, igual que el resto de la capa clínica.
- Registra un `AccessLog` antes de redirigir. Es obligatorio: es una lectura de
  dato clínico y las lecturas no emiten señales.
- Firma la URL y redirige, siguiendo el patrón de `ProtectedFileRedirectView`.

### Auditoría (deuda que hay que saldar aquí)

`ChatMessage` **no está registrado en el audit trail**. Con conversaciones
reales, esa tabla pasa a contener texto de salud de pacientes. Registrarlo desde
`AgentConfig.ready()`:

```python
audit.registry.register(ChatMessage, sensitive=['body', 'raw'])
```

Con `body` en `sensitive` queda constancia de que hubo un mensaje sin volcar su
contenido en el log.

### Criterios de aceptación de la fase 2

- Una foto entrante se ve en el hilo y **se sigue viendo una semana después**.
- La URL firmada caduca y deja de servir el objeto.
- Un usuario de otra clínica recibe 404 al pedir ese adjunto.
- Cada visualización deja su `AccessLog`.
- Un fichero que no es imagen ni audio se rechaza aunque venga con extensión
  `.jpg`.

---

## Fase 3 — Plantillas fuera de la ventana de 24 h

### Por qué esto es bloqueante ahora

Hoy, pasadas 24 h desde el último mensaje del paciente, el panel bloquea el
compositor y dice "habrá que esperar a que vuelva a escribir". Mientras la
clínica tenía la app en el móvil eso era un incordio. Sin la app, es un muro:
**no hay ninguna manera de avisar a un paciente de que mañana no se le puede
atender** si su último mensaje fue hace 30 horas.

Lo único que Meta deja mandar en esa situación es una plantilla aprobada.

### Qué hay que construir

**Modelo `MessageTemplate`**, por clínica: `name` (el nombre exacto en Meta),
`language`, `category`, `body_preview` (el texto con sus huecos, para
previsualizar), `variables` (lista ordenada) e `is_active`.

En v1 se dan de alta a mano: Elena las crea en Meta, nosotros las registramos
aquí. Sincronizarlas por la Graph API es una mejora posterior, no bloquea.

**`send_template()`** en `agent/whatsapp.py`, hermana de `send_text()`, con el
mismo tratamiento de errores.

**Interfaz:** cuando `can_send_free_text` es `False`, el compositor se sustituye
por un selector de plantillas con previsualización de cómo va a quedar el texto
con las variables rellenas. No dejar mandar a ciegas.

**Registro:** se guarda como un `ChatMessage` normal con
`message_type=TEMPLATE`, `sender=staff` y `body` con el texto ya renderizado,
para que el hilo se lea de corrido y no aparezca un identificador críptico.

### Dos cosas que hay que tener claras

- Mandar una plantilla **no reabre** la ventana de texto libre. La reabre la
  respuesta del paciente. La interfaz debe reflejarlo: tras enviar la plantilla,
  el compositor sigue bloqueado.
- Cada plantilla enviada cuenta contra el límite de conversaciones iniciadas de
  la clínica. Con una clínica de una persona no hay ningún riesgo de tocar
  techo, pero conviene saberlo.

### Criterio de aceptación

Con la ventana cerrada, la clínica puede mandar una plantilla, la ve en el hilo
como un mensaje más, y el compositor de texto libre sigue bloqueado hasta que el
paciente conteste.

---

## Fase 4 — Estados de entrega y badge

### Estados

`ChatMessage.status` hoy solo se pone al enviar. Para los ✓✓ hace falta que el
webhook `statuses` de Meta llegue a un endpoint nuevo:

`POST /api/agent/messages/status/` con `wa_message_id`, `status` y `timestamp`.

**Decisión: endpoint propio, no abrir `PATCH` en `ChatMessageViewSet`.** El hilo
es un registro append-only y `http_method_names` lo refleja a propósito. Un
endpoint específico que solo toca el campo de estado mantiene esa garantía.

Al actualizar, emite `chat_message` por el socket para que el ✓✓ aparezca en
vivo.

### Badge de no leídos en el sidebar

Hoy no existe. Si Elena está en Agenda, no se entera de que ha entrado un
mensaje. Implica conectar el socket desde `base.html` en todas las páginas, no
solo en la bandeja, y pintar el contador en `partials/_sidebar_nav.html`.

---

## Trabajo transversal

- **Configuración:** las variables de R2 (`R2_ACCESS_KEY_ID`, etc.) pasan a ser
  obligatorias en producción. Hasta ahora solo las necesitaban las fotos de
  lesiones; a partir de la fase 2 también los adjuntos de chat.
- **Verificar** que `CHANNEL_LAYERS` apunta a Redis DB 0 en el entorno de
  producción y que Daphne está sirviendo (no gunicorn).
- **Cada fase con sus tests**, en `tests/agent/`, siguiendo la estructura que ya
  existe.

---

## Fuera de alcance (a propósito)

Para que no haya dudas de qué no hay que hacer ahora:

- Sincronizar plantillas automáticamente desde la Graph API.
- Vídeo y documentos en el hilo.
- Importar el histórico previo de conversaciones.
- Buscador dentro de un hilo.
- Indicador de "escribiendo…".
- Coexistence. Cuando Propus se verifique en Meta y montemos Embedded Signup, se
  replantea; nada de lo de este plan se tira, porque el panel sigue siendo
  necesario para ver cómo trabaja el agente e intervenir.

---

## Resumen para arrancar

Si hay que empezar por algo hoy: **fase 1, y dentro de ella, trocear
`chat_inbox.html`**. Es lo que desbloquea todo lo demás y no depende de ninguna
decisión pendiente de Meta.
