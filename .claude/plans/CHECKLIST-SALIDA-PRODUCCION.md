# Checklist de salida a producción — Gaena

Creada el 05/10/2026. Para ir cerrándola estos días, paso a paso.
Marca cada casilla al terminarla (`- [x]`).

**Nota de partida: 6/10.**

- **Producto y agente: 8/10.** Funciona de punta a punta por WhatsApp real:
  personalidad, notas de voz, citas, modo humano, debounce, ✓✓, fotos y audios
  en el panel. Lo que ha salido en las pruebas era pulido y está arreglado. Le
  faltan horas de uso real.
- **Lo que impide salir: 4/10.** No es código: son trámites legales y de Meta,
  y algunos no dependen de nosotros.

**Plan:** una semana de rodaje con el número de prueba mientras se cierran en
paralelo lo legal y las plantillas. Cuando esté todo y el rodaje no saque nada
grave, se migra el número de Elena. Ahí: 8-9/10.

---

## 1. Legal y privacidad 🔴

Son datos de salud. Sin esto no se atiende a un paciente real.

- [ ] **E3 · Política de privacidad de Gaena**: pedírsela a Elena (o
      redactarla con ella) y ponerla en la app de Meta en lugar de la de Propus.
      *Quién: Xexu + Elena.*
- [ ] **Contrato de encargado del tratamiento** entre Gaena y Propus. *Quién:
      Xexu + Elena (revisarlo con quien lleve lo legal).*
- [ ] **Exención de supervisión de abusos de Azure** en `propus-openai-de` (el
      agente) y `propus-openai-se` (Whisper). Sin ella Microsoft puede guardar
      hasta 30 días lo que se envía al modelo. Es un formulario de Microsoft.
      *Quién: Xexu.*

## 2. Meta 🔴

- [ ] **M3 · Plantillas**: redactarlas con Elena (recordatorio de cita,
      confirmación…) y mandarlas a revisión. **Empezar ya: es lo que más tarda**
      y Meta puede rechazarlas. *Quién: Xexu + Elena.*
- [ ] **M6 · Método de pago en la WABA.** Meta lo exige. *Quién: Xexu / Elena.*
- [ ] **M2 · Verificar el portfolio de Elena** (🟡, conveniente). *Quién: Xexu
      + Elena.*

## 3. n8n y Django 🔴

- [ ] **N7 · Recordatorios por plantilla**: que el recordatorio de cita salga
      con la plantilla aprobada, que es lo único que Meta deja enviar pasadas
      24 h. Depende de M3. *Quién: Jesús / Xexu.*
- [ ] **D1 · Plantillas en el panel (fase 3)**: comprobar con Jesús en qué punto
      está. *Quién: Jesús.*
- [ ] **D2 · Suite de tests completa en verde.** *Quién: Jesús.*
- [ ] **Reactivar los workflows pausados**: recordatorios y
      `WA-Post-Visit-Followup` (Jesús los paró durante las pruebas). *Quién:
      Jesús, al terminar las pruebas.*
- [ ] **D4 · Notas de voz en el Safari del iPhone de Elena** (🟡): que pueda
      escucharlas en el panel. *Quién: Jesús.*

## 4. Rodaje con el número de prueba 🟡

Una semana, con el número de prueba (+1 555 156 9032), sin tocar el de Elena.

- [ ] Autorizar en Meta a **2 o 3 personas de confianza** (Paso 1. Probar →
      Destinatario → Administrar lista; máximo 5 números).
- [ ] Que **Elena** lo use como si fuera una paciente.
- [ ] Probar como **paciente nuevo** (que todavía no exista): la presentación
      sale una sola vez, contesta a lo que se pide y pide nombre, apellidos y
      correo.
- [ ] Pedir, cambiar y cancelar citas, con fechas claras y ambiguas.
- [ ] Notas de voz: claras, con ruido, en silencio, y audio seguido de texto.
- [ ] Fotos: llega el aviso y Elena la ve en el panel.
- [ ] Modo humano: Elena interviene, el agente se aparta y lo retoma después.
- [ ] Mensajes partidos (ráfaga): una sola respuesta.
- [ ] Revisar a diario el panel y los errores de n8n. Apuntar aquí lo que salga:

| Fecha | Qué ha pasado | Arreglado |
|---|---|---|
| | | |

## 5. Migración — el último paso 🔴

Solo cuando las secciones 1 a 4 estén cerradas.

- [ ] **E2 ·** Contarle a Elena el aviso que verán sus pacientes al cambiar a la
      API oficial.
- [ ] **E1 · Copia de los chats** de Elena antes de migrar.
- [ ] **M7 · Migrar el número de Elena** a la Cloud API.
- [ ] Repetir con la **WABA real** la suscripción a la app
      (`POST <WABA>/subscribed_apps`) — con la de prueba hizo falta.
- [ ] Cambiar en el panel (Agente → Configuración) el Phone Number ID y el token
      al número real, y comprobar el webhook.
- [ ] Primer mensaje real de prueba desde un móvil propio al número de Elena.
- [ ] Vigilar de cerca los primeros días.

---

## ⏰ Con fecha (no olvidar)

- [ ] **Primeros de noviembre de 2026**: decidir el sustituto de **Whisper**
      (se retira el **15/12/2026**). Opciones: Azure AI Speech en Germany West
      Central, o `gpt-4o-mini-transcribe` si ya existe en modo UE. Solo cambia
      el subflujo `WA-Audio-Transcribe`.
- [ ] **Cuando haya uso real**: vigilar la cuota de Whisper (3 peticiones por
      minuto). Si aparecen fallos por cuota, pedir más.
- [ ] **21/09/2027**: retirada de `gpt-5.4-mini`. Se actualiza solo;
      comprobar que la versión nueva siga en zona de datos UE.

---

## Hecho en las pruebas (30/09 – 05/10)

- [x] Circuito real por WhatsApp con el número de prueba.
- [x] Transcripción de notas de voz (Whisper, Sweden Central).
- [x] Negrita en formato WhatsApp (`Formatear respuesta`).
- [x] Sesiones duplicadas: parche en n8n y arreglo en Django (`b3f514c`).
- [x] Reintentos en `Cargar Config Clínica` (el 504 ya no pierde mensajes).
- [x] Presentación duplicada a pacientes nuevos: prompt + filtro `sinRepetir`.
- [x] Elena autorizada en el número de prueba.
