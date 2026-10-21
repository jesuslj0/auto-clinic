# Plan de mejoras de producto: fases 1 a 5

Estado a 21/10/2026. Documento autocontenido: se puede seguir sin más contexto.

## Por qué existe este plan

Apclinic (Superclinik, el programa de gestión de podología más extendido en
España, desde 2013) acaba de anunciar IA y una «Recepcionista IA» por WhatsApp
(módulo opcional de pago), y presenta su versión nueva en el Congreso Nacional de
Podología de Zaragoza (23-24/10/2026). Comparado el código de AutoClinic con su
web, el hueco más grave **no** es el agente (ahí estamos a la par o por delante)
sino que la parte más sólida del producto, la historia clínica, no se puede usar
desde el panel, y que faltan los avisos automáticos de cita. Este plan los cierra
por orden de impacto.

**Fuera del plan por decisión del 21/10/2026:** VeriFactu, coexistence de WhatsApp
y el dictado por voz de la nota clínica. No se construyen ni se prometen, ni a
clientes ni en la web. Gaena entra por migración completa del número.

## Reglas para todos los pasos

Resumen de `CLAUDE.md`; mandan sobre la comodidad.

- **Auditoría:** cada modelo clínico o con datos personales nuevo se registra en
  `audit.registry` desde su `AppConfig.ready()`, con el texto libre en `sensitive`.
  Cada vista que muestre dato clínico lleva `AccessLogMixin` (o `log_access()`).
- **Sin API:** la capa clínica no tiene REST a propósito; el `Api-Key` del agente
  no puede llegar a ella. Todo es vista de sesión con CSRF.
- **Nada se borra físicamente.** Soft delete; nunca `bulk_create`,
  `queryset.update()` ni `queryset.delete()` sobre un modelo registrado.
- **Multi-tenant:** un paciente de otra clínica es 404, no 403 (`PatientScopedMixin`).
- **Panel en español** (URLs en español, `name` en inglés, siempre con `{% url %}`);
  colores solo con los tokens de `_head_theme.html`, nunca `slate-*` ni `white`.
- **Tests** por paso en `tests/<app>/`, deterministas (fijan la hora, no usan
  «ahora»). Suite completa antes de dar nada por hecho:
  `POSTGRES_HOST=localhost pytest`.
- **Material comercial:** no se afirma nada de lo que no existe (ver
  `AUTOCLINIC-SECCIONES.md`, sección «Lo que NO hay que afirmar»). Al entregar un
  paso se actualizan `CLAUDE.md`, el README de la app y, aparte, los documentos
  comerciales.
- **Reparto:** Jesús = Django y n8n; Xexu = Meta, negocio y trato con Gaena. Las
  tallas (S ≤ 1 día, M 2-4 días, L 1-2 semanas) son una estimación a corregir por
  quien implemente.

---

# Fase 1: que la historia clínica se pueda usar

Empieza ya; no depende de nadie.

## 1.1 Curso clínico en el panel (M-L)

**Hoy.** `Episode`, `Visit`, `ClinicalNote` (SOAP) y `Addendum` existen, con
inmutabilidad en modelo y en trigger de PostgreSQL (`clinical/migrations/0002`) y
registrados en auditoría. **Ninguna vista ni formulario crea o muestra una
`ClinicalNote`**: solo el admin de Django y los seeds. Las visitas nacen hoy al
registrar un procedimiento (`clinical/procedures.py`: `record_procedure`,
`visit_for_appointment`, `visit_for_day`) o una observación de lesión.

**Qué hacer.**
- Pestaña nueva «Historia» en la ficha (añadir clave en `PATIENT_TABS`,
  `patients/views.py:256`; URL `pacientes/<id>/historia/`; hereda de `PatientTabView`).
- Lista de episodios, con sus visitas, notas y adendas, en orden cronológico.
- Alta de nota en **borrador**; editable mientras sea borrador; borrado lógico solo
  de borradores (`can_be_deleted()`).
- **Firmar** con `ClinicalNote.sign(professional)`: solo un usuario con
  `professional_profile`. La pantalla avisa de que no se podrá editar. Ningún
  endpoint de edición debe aceptar una nota firmada.
- **Adenda** sobre una nota firmada (`Addendum.objects.create`, solo inserción).
- Abrir la visita **desde una cita** (botón en la gestión de cita del panel):
  reutiliza `visit_for_appointment` y el episodio abierto, o abre uno con motivo.
- Cerrar y reabrir episodio (`Episode.close()` / `reopen()`); un episodio cerrado no
  admite visitas nuevas (el modelo ya lo impide).
- Todas las lecturas con `AccessLogMixin`.

**Decisiones previas.**
1. Etiquetas. El modelo es SOAP; Apclinic usa «Motivo / Diagnóstico / Tratamiento y
   pruebas / Evolución». Propuesta: mantener el modelo y mostrar etiquetas de
   podología sobre los mismos 4 campos (S «Motivo y lo que refiere», O «Exploración»,
   A «Diagnóstico», P «Tratamiento y plan»). **Validar con Elena antes de fijarlas.**
2. Autoría del borrador: `ClinicalNote` solo guarda `signed_by`. Quién escribió un
   borrador queda en el `ChangeLog`. Decidir si basta.

**Hecho cuando.** Un profesional puede abrir una visita desde una cita, escribir,
firmar y añadir adenda sin tocar el admin; una nota firmada no se puede cambiar por
ninguna vista; otra clínica ve 404; cada lectura deja `AccessLog`; hay tests de todo
eso.

## 1.2 Firmar consentimientos desde el panel (M)

**Hoy.** `ConsentTemplate`, `ConsentVersion` y `SignedConsent` existen
(`SignedConsent.sign(version, patient, episode, signature_image)` copia el texto
literal). La pestaña «Consentimientos» (`PatientConsentsTabView`) es **solo lectura**.
Plantillas y versiones se gestionan solo en el admin.

**Qué hacer.**
- Botón «Firmar consentimiento» en la pestaña. Pantalla propia: elegir plantilla con
  **versión vigente publicada** de la clínica del paciente, mostrar su texto
  literal, elegir episodio abierto (o abrir uno con motivo, igual que
  `EpisodeAuthoringMixin` en la anamnesis) y recoger la firma en un **lienzo táctil**
  (tablet y móvil; borrar y repetir; no se acepta vacío).
- POST multipart con la firma como imagen; el alta va **siempre** por
  `SignedConsent.sign()`. La validación por contenido ya ocurre en `save()`.
- La firma se sirve solo por `clinical:consent-signature`; nunca una URL del bucket
  en la plantilla.
- Gestión de plantillas y versiones: se queda en el admin por ahora.

**Hecho cuando.** Se firma desde el panel con el texto vigente; una versión en
borrador no se puede firmar; el texto firmado queda copiado; la lectura deja
`AccessLog`; tests.

## 1.3 Ficha y RGPD desde el móvil del paciente (L; después de 1.1 y 1.2)

**Objetivo.** El paciente rellena anamnesis, lee la información de protección de
datos y firma desde su móvil antes de la visita.

**Hoy.** `QuestionnaireResponse.Source.PATIENT_WEB` existe en el modelo, pero no hay
ninguna vista pública. `created_by` es opcional a propósito.

**Qué hacer.**
- Modelo nuevo de **enlace de ficha** (clínica, paciente, token aleatorio de un solo
  uso, caducidad corta, usado o no), registrado en auditoría.
- Ruta pública en español (p. ej. `/ficha/<token>/`) fuera de `/api/`. La clínica
  genera el enlace desde la ficha y se lo envía por el chat (en ventana de 24 h, o
  con una plantilla en un paso posterior).
- La página muestra solo el cuestionario vigente, el texto de consentimiento y el
  lienzo de firma, **nunca nada de la historia**. Escribe con
  `QuestionnaireResponse.record(source=PATIENT_WEB)` (que dispara las alertas) y
  `SignedConsent.sign()`.
- Cabeceras: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `noindex`;
  límite de intentos; token que no se pueda tantear.

**Por resolver.**
- **Cómo registra `AccessLog` una vista sin usuario.** Comprobarlo en `audit/`; si
  no lo admite, decidir cómo se audita (p. ej. el actor es el enlace).
- Menores y contactos responsables (`Guardian`): quién firma.
- El texto informativo de protección de datos es de la clínica; revisarlo con quien
  lleve lo legal antes de ofrecerlo.

---

# Fase 2: datos para facturar (reducida)

VeriFactu está fuera del plan. Esta fase solo prepara datos.

## 2.1 NIF y dirección del paciente (S)

**Hoy.** `Patient` no tiene NIF ni dirección (solo nombre, correo, teléfono, fecha de
nacimiento, notas y foto).

**Qué hacer.** Campos opcionales `tax_id` (NIF/NIE/pasaporte) y dirección en
`Patient`, en el formulario del panel, **sin exponerlos en el serializer de la API**
(el agente no los necesita), y añadidos a `sensitive` en `patients/apps.py`.

**Hecho cuando.** Migración, formulario, auditoría y tests; el serializer no los
devuelve.

## 2.2 Factura como documento: EN ESPERA

No se construye hasta tener la respuesta del gestor sobre si podemos seguir
ofreciendo el módulo de facturación sin VeriFactu (riesgo del productor). Cuando se
retome: vista imprimible o PDF con emisor (`Clinic`), receptor (NIF **copiado en el
momento de emitir**, igual que ya se copian las líneas), número, fecha, líneas,
mención de exención que indique el asesor y total; envío por email y WhatsApp.
`PatientInvoice` hoy solo congela `frozen_patient_name`.

---

# Fase 3: comunicación automática

Reutiliza el sistema de plantillas de WhatsApp ya entregado
(`agent.WhatsAppTemplate`, `notifications/whatsapp_reminders.py`).

## 3.1 Avisos de cita: confirmación, cambio y cancelación (M)

**Hoy.** Lo único que Django manda solo es el recordatorio de 24 h. Una cita creada,
movida o cancelada desde el panel no avisa al paciente.

**Qué hacer.**
- Propósitos nuevos en `WhatsAppTemplate.Purpose` (`agent/models.py:555`):
  `booking_confirmed`, `booking_changed`, `booking_cancelled`. `purpose` tiene
  `max_length=20`: no pasar de ahí. Una plantilla por propósito y clínica; **sin
  plantilla aprobada marcada = ese aviso desactivado** (mismo interruptor que el
  recordatorio).
- Enganchar en `appointments/services.py`: `create_appointment` (L380),
  `confirm_by_clinic` (L735), `reschedule_appointment` (L805) y `cancel_appointment`
  (L785), con `transaction.on_commit` y una tarea de Celery (no llamar a Meta dentro
  de la petición del panel). Módulo nuevo `notifications/whatsapp_notices.py`, al
  estilo de `whatsapp_reminders.py`.
- **Quién lo dispara:** solo cuando el cambio lo hace la clínica (`actor=STAFF`). Si
  lo pidió el paciente (por el agente o por el enlace) ya recibió respuesta, y no se
  duplica. Una cita del agente nace `pending`; el aviso de «confirmada» sale cuando
  la clínica la valida con `confirm_by_clinic`.
- Variables: la lista cerrada de `appointments/reminder_fields.py` (sin servicio ni
  dato clínico). El aviso de cambio necesita además la hora anterior: capturarla
  antes de que `reschedule_appointment` la sobrescriba.
- Mismas protecciones que el 24 h: bloqueo de fila, tope de intentos, fallo visible
  en el hilo (generalizar `record_unsent_template`, `agent/services.py:336`, que hoy
  tiene el texto «Recordatorio de cita» fijo), sin envío con variable vacía, y
  `booked_for_other` respetado (a quién se saluda y qué teléfono).
- Cada aviso es una plantilla de utilidad de pago: avisar para recalcular los costes
  de WhatsApp en `PRICING.md`.

**Hecho cuando.** Cuatro eventos con su plantilla configurable, no duplican lo que el
agente ya contestó, no se envía sin teléfono ni sin plantilla, y hay tests de cada
camino.

## 3.2 Segundo recordatorio opcional: 3 h o 48 h antes, a elegir por la clínica (M)

**Decisión (21/10/2026).** El recordatorio de 24 h es la base. Además, cada clínica
puede activar **un** recordatorio extra, a **3 h** o a **48 h** de la cita, o
ninguno. Gaena solo usa el de 24 h por ahora.

**Hoy.** `Appointment` tiene `reminder_24h_sent` y `reminder_3h_sent` (con su `_at`)
y `appointments/reminders.py` ya sabe calcular las citas pendientes de 24 h y 3 h
(`pending_reminder_queryset`, con `DEFAULT_WINDOWS`). **No existe nada de 48 h.** El
workflow de n8n `WA-Reminder-3h-Followup-Scheduler` sigue apagado y no se reactiva.

**Qué hacer.**
- Ajuste de clínica `Clinic.extra_reminder` con las opciones: ninguno (por defecto),
  3 h, 48 h. Visible solo para administradores, junto a la configuración del agente.
- Propósitos de plantilla nuevos `reminder_3h` y `reminder_48h`
  (`WhatsAppTemplate.Purpose`, `agent/models.py:555`; `purpose` tiene `max_length=20`).
  Una plantilla aprobada marcada por propósito y clínica; sin ella, ese recordatorio
  no sale (el mismo interruptor que el de 24 h). Si la clínica elige 3 h y no hay
  plantilla `reminder_3h` aprobada, el panel lo avisa.
- Para 48 h: campos `reminder_48h_sent` y `reminder_48h_sent_at` en `Appointment`
  (migración), la ventana en `DEFAULT_WINDOWS`, y **ponerlos a vacío al reprogramar**
  (`reschedule_appointment` ya limpia los de 24 h y 3 h; añadir los de 48 h).
- Tarea de Celery y módulo al estilo de `notifications/whatsapp_reminders.py`
  (ventana con margen para aguantar una caída, bloqueo de fila, tope de intentos,
  fallo visible en el hilo). Una cita reservada con menos antelación que la del
  recordatorio simplemente no lo recibe.
- Regla de a quién se envía: solo a quien **no ha confirmado** ya
  (`patient_confirmed_at` vacío), igual que hoy. El de 3 h sale solo si el de 24 h ya
  salió y no hubo respuesta.
- Mismos datos permitidos que el de 24 h (`appointments/reminder_fields.py`): nada
  clínico ni el servicio.

**Ojo con n8n (Jesús).** El orquestador decide que un «sí» del paciente responde al
recordatorio con `reminder_24h_sent=true&reminder_responded=false&status=confirmed`.
Con el recordatorio de 48 h, el paciente puede contestar **antes** de que haya salido
el de 24 h, y ese «sí» no se reconocería. Hay que ampliar ese filtro (o exponer un
único indicador de «hay un recordatorio pendiente de respuesta») antes de activar
el de 48 h.

**Hecho cuando.** La clínica elige entre ninguno, 3 h y 48 h; solo sale el elegido,
con su plantilla, a quien no confirmó; una cita movida vuelve a recibirlo; el «sí» al
de 48 h lo registra el agente; hay tests de cada camino.

## 3.3 Botones «Confirmar / Cancelar» en el recordatorio (M-L)

**Hoy.** El paciente contesta «SÍ» o «NO» y lo gestiona el agente
(`register_patient_confirmation`). `build_components` (`agent/wa_templates.py`) solo
soporta cabecera de texto, cuerpo y pie, y `sync_templates` deja en solo lectura las
plantillas con botones.

**Qué hacer.** Soportar botones de respuesta rápida en el constructor y la
sincronización; enviar el payload en cada envío; y que el orquestador de n8n
(`Normalizar Mensaje`) reconozca la respuesta de botón y llame a
`POST /api/public/appointments/<token>/confirm|cancel/`, que ya existe. Reflejar la
respuesta en el hilo. Es trabajo de Django **y** de n8n. Los límites y el formato del
payload hay que verificarlos en la documentación de Meta.

## 3.4 Reseña de Google y recordatorio de revisión (L; bloqueado por revisión legal)

**Hoy.** Existe un workflow de n8n, `WA-Post-Visit-Followup`, apagado; no se ha
revisado su estado en esta auditoría.

**Qué hacer.** Llevarlo a Django con el patrón de 3.1: `Clinic.google_review_url`,
un intervalo de revisión por servicio y un consentimiento comercial del paciente
(campo con fecha y origen, auditado, capturable en 1.3 y en el panel). **Antes de
enviar nada hay que cerrar la base legal** de comunicaciones comerciales y de
seguimiento con quien lleve la protección de datos, y tener un mecanismo de baja. No
se envía a quien no haya consentido.

---

# Fase 4: IA clínica (reducida)

El **dictado por voz de la nota clínica no se implementa** (decisión del
21/10/2026).

## 4.0 Transcripción de las notas de voz de WhatsApp: decide Xexu

Whisper (`propus-openai-se`, Sweden Central) **se retira el 15/12/2026** y lo usa el
subflujo de n8n `WA-Audio-Transcribe`, que transcribe las notas de voz de los
pacientes. Xexu valora el sustituto en su siguiente conversación (opciones: Azure AI
Speech en Germany West Central, o `gpt-4o-mini-transcribe` si Microsoft lo ofrece ya
en zona de datos UE). Solo afecta a n8n; no hay trabajo en Django hasta que decida.

Microsoft **denegó** la exención de supervisión de abusos de Azure (octubre 2026):
puede conservar lo que se le envía hasta 30 días. No prometer a clientes que «el
audio no se guarda» sin matizar qué se guarda en Azure, y reflejarlo en la política de
privacidad.

## 4.1 Informes de paciente con IA (L; después de 1.1)

Informe redactado a partir del curso clínico, en castellano, catalán, euskera o
gallego, editable por el profesional, con modelo propio e inmutable una vez emitido
(texto y hash congelados, auditado, sin API), y entregable en PDF por email o
WhatsApp.

- El profesional siempre revisa y emite; la IA solo propone el borrador. El prompt
  prohíbe inventar o diagnosticar.
- Hoy Django no habla con ningún proveedor de IA (todo va por n8n). Decidir entre
  Jesús y Xexu si el borrador lo genera Django directo contra Azure desde un único
  módulo aislado o pasa por n8n. Región UE en cualquier caso.
- Exige elegir librería de PDF (compartida con 2.2) y ampliar el envío saliente de
  WhatsApp a documentos (`send_staff_media` solo admite imagen y audio).

---

# Fase 5: el resto, por demanda real y poco a poco

Se implementa **cuando lo pida un cliente o un prospecto** (Elena, los primeros
clientes). Si no hay demanda, avanzamos nosotros **poco a poco, día a día**, en el
orden de la tabla, sin esperar. No se copia la lista de Apclinic por copiarla.
Orden sugerido:

| # | Qué | Por qué / dependencias |
|---|---|---|
| 5.1 | Importación y exportación de datos | Apclinic migra gratis; en `PRICING.md` sigue pendiente «¿existe exportación?». Reduce la fricción de entrada y responde a «¿y si me quiero ir?» |
| 5.2 | Presupuestos y mutuas | Mutuas (p. ej. MUFACE) relevantes en podología; presupuesto desde la ficha |
| 5.3 | Recetas | Modelo oficial con XML del colegio: preguntar a Elena de qué colegio es |
| 5.4 | Cita online por web | Se eliminó la reserva pública el 06/10 (app `booking`); `Appointment.Source.BOOKING` sigue en el modelo. Reintroducir solo si hay demanda |
| 5.5 | Roles y permisos por persona | Hoy solo administrador y staff |
| 5.6 | Agenda: arrastrar y soltar; fotos desde el móvil por QR; dibujo sobre plantilla | Mejoras de uso |
| 5.7 | Sala de espera, fichaje, comisiones, gastos | Los gastos habilitarían un escáner de facturas con IA |

---

## Orden y dependencias

```
1.1 ──► 1.3 ◄── 1.2          2.1 (independiente)        2.2 en espera del gestor
 │                            3.1 ──► 3.3
 └──► 4.1                     3.2 (necesita ajuste en n8n)
                              3.4 (tras revisión legal)
4.0 lo decide Xexu (solo n8n)
5.x por demanda y, si no, poco a poco
```

Empezar por **1.1 y 1.2** en paralelo, y **3.1** en cuanto haya hueco. Al terminar
cada paso, actualizar este documento marcándolo hecho.

## Decisiones abiertas

1. Etiquetas de la nota clínica (1.1): SOAP o las de podología. Preguntar a Elena.
2. Autoría de los borradores (1.1).
3. Cómo se audita una vista pública sin usuario (1.3).
4. Informes con IA (4.1): Django directo contra Azure o vía n8n.
5. Librería de PDF (2.2 y 4.1).
6. Base legal de las comunicaciones comerciales (3.4).
7. Cómo deja n8n de depender de `reminder_24h_sent` para reconocer el «sí» al
   recordatorio (3.2), antes de activar el de 48 h.

Decidido y cerrado: recordatorio extra a 3 h o 48 h a elección de la clínica (3.2);
sin dictado por voz; sustituto de Whisper lo decide Xexu (4.0).
