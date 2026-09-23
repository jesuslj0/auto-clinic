# AutoClinic — guía de secciones para la web de Propus

Documento de trabajo para quien construya la página de AutoClinic en
**propus.ink**. Describe cada pantalla del panel, qué se ve en ella y qué se
puede hacer, para que las capturas vayan acompañadas de un texto que sea cierto.

Está escrito a partir del código real, no de un folleto. Si algo no aparece
aquí, probablemente no exista todavía: hay al final una lista de cosas que **no**
hay que afirmar.

---

## 1. Qué es AutoClinic, en una frase

Un software de gestión para clínicas que combina dos mitades que normalmente se
venden por separado: **un agente de WhatsApp que atiende a los pacientes 24 h** y
**una historia clínica electrónica completa**, con la agenda y la facturación
cosidas entre medias. Está construido para podología, que es el nicho de partida,
pero la estructura es válida para cualquier consulta con cita previa.

La página actual de propus.ink cuenta bien la primera mitad, la del agente. Lo
que estas capturas añaden es la segunda: que detrás del bot hay un programa de
gestión de verdad, y que eso es lo que justifica el precio.

---

## 2. Cómo usar este documento con las capturas

Hay 24 capturas, tomadas en modo claro y en modo oscuro. El panel tiene un
sistema de temas real, no un filtro: los colores están definidos como variables
semánticas y el conmutador reasigna la paleta entera, así que las dos versiones
de cada captura son la misma pantalla, no dos maquetas.

Sugerencia de montaje para la web: un conmutador claro/oscuro **global** de la
sección, no uno por imagen. Si el visitante cambia el modo, todas las capturas de
la página cambian a la vez. Es la forma de que se lea como un producto y no como
una galería.

Orden recomendado de presentación, de lo más vendible a lo más técnico:

1. Panel de control
2. Chats
3. Agenda
4. Ficha del paciente
5. Facturación
6. El resto de la configuración

Cada bloque de abajo trae un titular corto que puedes usar tal cual y un párrafo
de apoyo.

---

## 3. Las secciones, una por una

### 3.1 Acceso

**Titular:** Una cuenta por persona, no una contraseña compartida.

Pantalla de entrada con correo y contraseña. El identificador es el correo
electrónico, no un nombre de usuario inventado. Cada miembro del equipo tiene su
propia cuenta con un rol, administrador o personal, y todo lo que hace queda
atribuido a esa cuenta.

A la derecha de la pantalla hay un panel de marca con el argumento del producto,
que es el sitio natural donde poner el logotipo de la clínica cliente en una
demo.

---

### 3.2 Panel de control

**Titular:** Lo que pasa hoy, y cuánto se ha cobrado este mes.

Es la pantalla de inicio. Reúne tres cosas:

**Las citas de hoy.** El listado del día con su hora, paciente, servicio,
profesional y estado. Desde aquí se confirma, se completa, se rechaza o se marca
como no presentado sin entrar en ninguna otra pantalla.

**Las tarjetas de actividad.** Citas de hoy y cuántas van completadas, citas
pendientes de validar, canceladas del mes y pacientes nuevos del mes.

**El bloque económico.** Cobrado en el mes, facturado en el mes, pendiente de
cobro y trabajo hecho que todavía no se ha facturado, más una gráfica de barras
con lo que entró cada día y la comparación contra el mismo tramo del mes
anterior.

Un detalle que conviene contar porque es un argumento de confianza: el panel
suma **cobros reales**, no el precio de catálogo de las citas completadas. Es la
diferencia entre «esto es lo que has ingresado» y «esto es lo que deberías haber
ingresado». Subir la tarifa mañana no reescribe los ingresos de enero.

Distingue además tres cosas que muchos programas mezclan: lo facturado, lo
cobrado y lo que está hecho pero aún sin factura.

---

### 3.3 Citas

**Titular:** Todas las citas, con su historia detrás.

Listado filtrable por fecha y por estado, ordenable y paginado. Por defecto
enseña el día de hoy.

Los estados de una cita son seis: pendiente, confirmada, completada, cancelada,
reagendada y no presentado. Cada cambio de estado queda registrado con quién lo
hizo y cuándo, así que una cita no es solo su situación actual sino su recorrido
completo.

Cada cita guarda también **de dónde vino**: del panel, del agente de WhatsApp o
de la reserva pública. Eso es lo que permite enseñarle al cliente cuánta agenda
le está llenando el bot.

El alta manual desde el panel valida en el momento que el profesional presta ese
servicio, que trabaja a esa hora y que no tiene otra cita encima.

---

### 3.4 Agenda

**Titular:** La semana entera, por profesional.

Calendario semanal con las citas colocadas sobre el horario real de cada
profesional. Las citas que se solapan se reparten en columnas en vez de taparse.

El horario admite **jornada partida**, varios tramos el mismo día, que es como
trabaja de verdad una consulta con parón de mediodía. Contempla también las
ausencias puntuales: vacaciones, bajas, formación o una reunión de dos horas un
martes.

Quien tiene ficha de profesional ve su propia agenda. Un administrador de
clínica ve la de todo el equipo.

Hay un punto técnico que merece mencionarse en la web porque es una fuente
clásica de errores en este tipo de producto: el horario semanal se guarda en
**hora local de la clínica**, no en UTC, porque «las nueve de la mañana» sigue
siendo las nueve tanto en horario de invierno como de verano.

---

### 3.5 Pacientes

**Titular:** La ficha, no una lista de teléfonos.

Listado con búsqueda por nombre, correo o teléfono. Los teléfonos se normalizan
a formato internacional al darlos de alta, de modo que el mismo número escrito
de tres maneras distintas no genera tres fichas.

Al dar de alta un paciente se le abre automáticamente su historia clínica, con
número correlativo por clínica y año. Nunca existe un paciente sin historia.

---

### 3.6 Ficha del paciente

**Titular:** Historia clínica completa, no un campo de notas.

Es la parte más densa del producto y la que más lo separa de una agenda con bot.
Son seis pestañas, cada una con su dirección propia, así que se puede enlazar y
recargar sin perder el sitio.

Encima de todas ellas hay una **banda de alertas** siempre visible, ordenada por
gravedad: lo que puede contraindicar un tratamiento se ve antes de abrir nada.

**Datos generales.** Los datos administrativos y el resumen de su actividad.

**Anamnesis.** Los cuestionarios que ha contestado el paciente. El cuestionario
está **versionado**: cambiar una pregunta significa publicar una versión nueva,
nunca editar la antigua. Y lo que se guarda de una respuesta no es un enlace a
las preguntas, sino una **copia literal** del texto que se le mostró y de lo que
contestó. Retocar el cuestionario mañana no puede reescribir lo que alguien
respondió el año pasado.

**Alertas.** Los avisos vigentes del paciente. Se derivan solas de la anamnesis:
si declara diabetes, anticoagulación o alergia a los anestésicos locales, la
alerta aparece sin que nadie la teclee. Una alerta corregida se desactiva, nunca
se borra, para que siga siendo posible responder a «qué se sabía en aquel
momento».

**Lesiones.** Un mapa del pie sobre el que se marcan las lesiones, con vistas
dorsal, plantar, medial y lateral, y los dos pies. Cada lesión guarda por
separado su zona anatómica codificada, que es el dato clínico, y sus coordenadas
sobre el dibujo, que son solo para pintar.

Lo importante de esta pestaña es que **una lesión es una serie, no un dato**. De
cada visita se registran las medidas en milímetros y la foto de ese día, y la
ficha ofrece la evolución en orden y la comparación de dos momentos lado a lado.
Es lo que convierte «tiene una úlcera» en «la úlcera ha pasado de 14 a 5 mm en
seis semanas».

**Consentimientos.** Los consentimientos informados que ha firmado, con la misma
lógica de versionado que la anamnesis: se guarda el **texto literal** que se
firmó, no una referencia al documento actual. Republicar el consentimiento no
cambia lo que alguien aceptó.

**Procedimientos.** Qué se le ha hecho y por cuánto. Cada procedimiento
**congela** el nombre y el precio del servicio el día que se realizó, y no vuelve
a mirar el catálogo. Una subida de tarifas no reescribe lo que costaron los
tratamientos del año pasado.

---

### 3.7 Chats — la sección que hay que contar bien

**Titular:** Recuperas el WhatsApp de la clínica.

Esta pantalla resuelve un problema que el cliente no ve venir hasta que ya ha
contratado el bot, y explicarlo antes es un argumento de venta muy fuerte.

**El problema.** Cuando conectas un número a la API oficial de WhatsApp Business,
ese número **deja de funcionar en la aplicación normal**. No puedes abrir
WhatsApp en el móvil de la clínica y leer lo que te ha escrito un paciente: el
número ya no vive ahí, vive en la API. Mucha gente descubre esto el primer día y
siente que ha perdido el control de su propio teléfono.

**La solución.** La bandeja de chats **es** ese WhatsApp. Devuelve la ventana a
las conversaciones, con el añadido de que ahora esa ventana está dentro del
programa de gestión, al lado de la ficha del paciente.

Cómo está montada la pantalla:

**Lista de conversaciones a la izquierda.** Con avatar, nombre del paciente si el
número coincide con una ficha, vista previa del último mensaje, fecha y contador
de mensajes sin leer. Se puede buscar por teléfono, por paciente o por el
contenido del último mensaje, y filtrar por «solo sin leer».

**El hilo a la derecha.** Burbujas al estilo WhatsApp, separadores de día,
imágenes y adjuntos, hora de cada mensaje y su estado de entrega. Los mensajes se
distinguen por color según quién los escribió: el paciente, el agente o una
persona del equipo. Eso último es clave: **en el hilo se ve qué contestó la IA y
qué contestó un humano**, lo cual no es un detalle estético sino trazabilidad.

**Un cuadro de texto para responder a mano.** Se escribe y se envía, igual que en
WhatsApp. El envío va **directo a la API de Meta**, sin pasar por el motor del
agente, precisamente para que la clínica pueda seguir hablando con sus pacientes
aunque el bot esté caído.

#### El control agéntico-humano

Esto es lo más vendible de la sección y conviene dedicarle su propio bloque en la
web. Hay **tres niveles de control**, de más fino a más grueso:

**Nivel 1 — Escribir aparta al agente solo.** No hace falta pulsar nada previo.
En cuanto una persona escribe en un hilo, el agente se calla en ese hilo para que
no contesten los dos encima. Y **lo retoma solo** tras unos segundos de
inactividad, un plazo que configura la clínica. La pantalla avisa: «Has
intervenido en la conversación, el agente la retoma en X si no vuelves a
escribir».

**Nivel 2 — Modo humano por conversación.** Un conmutador en la cabecera del hilo
que alterna entre «Agente IA» y «Modo humano». Esta pausa es explícita y **no
caduca**: el hilo se queda en manos de la persona hasta que alguien lo devuelva.
Sirve para la conversación delicada, la reclamación, el caso raro.

**Nivel 3 — Interruptor general de la clínica.** Un botón en la cabecera de la
sección que apaga el agente en **todos** los chats a la vez. Útil para una
incidencia, para un festivo o simplemente para probar.

Los tres niveles se leen en una sola respuesta, la misma que consulta el motor
del agente antes de generar nada, así que lo que ve el usuario en pantalla y lo
que hace el bot no pueden desincronizarse.

Cuando el agente vuelve a tomar un hilo, **recibe además los últimos mensajes**
que escribió la persona. Sin eso retomaría la conversación sin saber lo que
acaba de decir su compañera humana, que es exactamente el fallo que hace que los
bots híbridos queden mal.

#### La ventana de 24 horas

Un detalle honesto que da credibilidad si se cuenta: WhatsApp solo permite
responder con **texto libre durante las 24 horas siguientes** al último mensaje
del paciente. Fuera de ese plazo hacen falta plantillas aprobadas por Meta.

El panel no disimula esa regla: cuando el plazo ha pasado, el cuadro de texto se
bloquea y explica por qué en vez de dejar escribir un mensaje que no va a llegar.
Y si Meta rechaza un envío, el mensaje se queda en el hilo marcado como fallido
con el motivo, en lugar de aparentar que salió.

**Un enlace a la ficha.** Desde la cabecera del hilo se salta a la ficha clínica
del paciente con un clic. Ese es el argumento que ningún WhatsApp normal puede
dar: estás hablando con alguien y tienes su historia al lado.

---

### 3.8 Servicios

**Titular:** El catálogo que el agente sabe vender.

Catálogo por clínica, organizado en categorías con color propio. Una clínica
nueva arranca ya con las categorías de podología creadas, y puede renombrarlas o
añadir las suyas.

Cada servicio tiene duración y precio, y ambos pueden ser **fijos o variables**.
Un servicio variable se muestra como «180 – 260 €» o como «desde 180 €», que es
como se anuncian de verdad unas plantillas personalizadas. Cuando la duración es
variable, la agenda reserva el máximo, que es lo único que evita que dos citas
seguidas se pisen si la primera se alarga.

Este catálogo es el que consulta el agente para responder precios y para calcular
cuánto hueco ocupa cada cita.

---

### 3.9 Facturación

**Titular:** Del tratamiento a la factura, sin teclear dos veces.

Listado con las métricas arriba y las facturas abajo, las dos mitades alimentadas
por el mismo filtro: lo que dice el total es siempre el total de lo que se está
viendo.

El circuito es el que sigue. Los procedimientos realizados quedan **pendientes de
facturar**. Se agrupan en un borrador de factura, que se compone y se recalcula
libremente. Al **emitirla** se cierra: toma número de la serie correlativa de la
clínica, copia sus líneas literalmente y fija el importe. A partir de ahí no se
toca.

Una factura emitida **no se corrige, se anula y se emite otra**. El número se
queda gastado, que es lo que exige una serie correlativa, y la anulada sigue
siendo legible entera porque su contenido está copiado, no referenciado.

Los cobros son documentos aparte, con su propio número de recibo. Una factura
puede cobrarse en varias veces, y el listado distingue impagada, parcial y
pagada. Los métodos son tarjeta, transferencia, Bizum y efectivo.

Hay dos protecciones que conviene mencionar porque son las que fallan en los
programas caseros: **no se puede cobrar más de lo que se debe**, ni siquiera con
dos cobros simultáneos, y **no se puede anular una factura que ya tiene cobros**.

---

### 3.10 Profesionales

**Titular:** Quién trabaja, cuándo y en qué.

Alta y edición de las fichas del equipo: tipo de profesional, foto, qué servicios
presta, si acepta reservas online, margen entre citas y granularidad de los
huecos.

Aquí se configura el horario semanal por tramos y las ausencias. Es lo que
alimenta tanto el calendario como el motor que decide qué huecos puede ofrecer el
agente.

La distinción entre «no acepta reservas online» y «no está activo» es
deliberada: la primera significa que el bot no puede ofrecerlo, pero el equipo sí
puede asignarle citas a mano.

---

### 3.11 Clínica

**Titular:** Los datos que el agente da por teléfono.

Ficha de la clínica: nombre, NIF, contacto, dirección, web, logotipo y zona
horaria. Es lo que el agente usa para responder dónde estáis y cómo llegar.

Incluye también la configuración de cuánto tiempo se le guarda el hueco a una
cita que el agente ha reservado y que el equipo aún no ha validado. Pasado ese
plazo la cita se cancela sola y el hueco se libera, de modo que una reserva sin
confirmar no bloquea la agenda para siempre.

---

### 3.12 Base de conocimiento

**Titular:** Lo que el agente sabe, escrito por la clínica.

Entradas de texto organizadas por tipo: horarios, ubicación, precios, servicios,
políticas, equipo y preguntas frecuentes. Es la fuente de la que bebe el agente
para contestar cualquier cosa que no sea agendar.

Se edita desde el panel, sin tocar el bot. Es el argumento de que la clínica
controla lo que dice su asistente sin depender de nadie.

---

### 3.13 Agente de WhatsApp

**Titular:** Conectar el número, y probarlo antes de soltarlo.

Pantalla de configuración guiada de la integración con la WhatsApp Cloud API:
identificador del número, token de acceso y token de verificación del webhook,
con la dirección del webhook lista para pegar en Meta. La pantalla indica si la
conexión está completa.

Lo más enseñable de esta sección es el **chat de pruebas**: un cuadro de
conversación dentro del panel que habla con el agente real usando un paciente de
prueba, sin gastar mensajes de WhatsApp y sin molestar a nadie. Sirve para
comprobar qué contesta el bot antes de ponerlo delante de pacientes, y para
verificar que un cambio en la base de conocimiento ha surtido efecto.

Ese hilo de pruebas se guarda, pero queda **fuera de la bandeja de chats** y no
cuenta en los mensajes sin leer.

---

### 3.14 Mi cuenta

**Titular:** Cada uno gestiona lo suyo.

Tres pestañas: los datos de la cuenta y el cambio de contraseña, la ficha
profesional propia con foto y servicios, y el horario y las ausencias propias.

El cambio de contraseña tiene límite de intentos con bloqueo temporal, y el
cambio queda registrado en el rastro de auditoría, que es el único sitio donde
podría constar, porque la contraseña en sí nunca se escribe en ningún log.

---

### 3.15 Reserva pública

**Titular:** Para quien prefiere no escribir.

Un flujo de reserva web sin necesidad de cuenta: elegir servicio, elegir día y
hora entre los huecos realmente libres, y confirmar. Usa el mismo motor de
disponibilidad que el agente, así que no puede ofrecer un hueco que el bot acaba
de dar.

Existen además enlaces de un solo uso para que el paciente **confirme o cancele**
su cita desde el recordatorio, sin autenticarse.

---

## 4. Tres argumentos transversales que valen para toda la página

### 4.1 Nada se borra

Toda la capa clínica usa borrado lógico. Una nota clínica firmada **no admite
cambios ni borrado**, y eso no está solo programado en la aplicación: está
impuesto por un disparador en la propia base de datos, de modo que ni siquiera el
acceso directo por SQL puede saltárselo. Lo único que se puede hacer con una nota
firmada es añadirle una adenda, que también es de solo inserción.

Es el argumento de cumplimiento normativo más fuerte que tiene el producto:
historia clínica bajo la Ley 41/2002 y datos de salud como categoría especial
del RGPD.

### 4.2 Todo queda registrado, sin copiar datos clínicos

Hay un rastro de auditoría doble. Por un lado, cada escritura sobre un dato
clínico o de facturación queda registrada con quién, qué y cuándo. Por otro, y
esto es lo que casi nadie implementa, **también quedan registradas las
lecturas**: quién abrió la ficha de qué paciente.

El texto clínico libre va marcado como sensible: el registro guarda **que** un
campo cambió, nunca su contenido. El log no puede ser una segunda copia sin
cifrar de la historia.

### 4.3 Los datos de una clínica no salen de su clínica

Todo el sistema está construido en multi-inquilino: cada consulta está acotada a
la clínica de quien pregunta, y el agente automatizado solo alcanza los datos de
la suya mediante su propia clave.

La capa clínica, además, **no tiene API REST a propósito**. La clave del agente
puede crear y mover citas, pero no existe ningún camino desde el bot hasta la
historia clínica. Las fotos de lesión y las firmas viven en un almacén privado y
solo se sirven mediante enlaces firmados que caducan, previa comprobación de
permisos.

---

## 5. Vocabulario y cifras

Para mantener coherencia en toda la página.

| Concepto | Cómo nombrarlo |
|---|---|
| El producto | AutoClinic |
| El bot | el agente, o el agente de WhatsApp |
| La pantalla de inicio | Panel de control |
| El calendario semanal | Agenda |
| El listado de citas | Citas |
| La bandeja de WhatsApp | Chats |
| La historia del paciente | Ficha del paciente |
| Pausa por conversación | Modo humano |
| Pausa global | Interruptor general del agente |

Datos exactos que se pueden afirmar sin miedo:

- Seis estados de cita: pendiente, confirmada, completada, cancelada, reagendada
  y no presentado.
- Tres orígenes de cita: panel, agente de WhatsApp y reserva pública.
- Seis pestañas en la ficha del paciente.
- Cuatro vistas del mapa del pie y los dos pies.
- Recordatorios automáticos por WhatsApp a 24 h y a 3 h de la cita; el segundo
  solo a quien no confirmó el primero.
- Cuatro métodos de cobro: tarjeta, transferencia, Bizum y efectivo.
- La ventana de texto libre de WhatsApp es de 24 h.

---

## 6. Lo que NO hay que afirmar

**Esta sección es interna. Nada de lo que hay aquí se publica.**

No es una lista de limitaciones que confesar en la página: es una lista de
afirmaciones que todavía no tocan, para que no se escriba una promesa que el
producto aún no cumple. Una web de producto no habla de lo que le falta, ni
tiene por qué. Se omite y ya está.

Dicho de otro modo: de estos puntos, lo correcto es **callar**, nunca aclarar.

- **Tiempo real: aún no, pero llega antes de producción.** El refresco
  automático de los chats está pendiente y se resuelve antes del lanzamiento.
  Hasta que esté desplegado, no construyas un titular ni un bloque entero que
  dependa de ello («mensajes instantáneos», «notificaciones push»). Cuando se
  suba, este punto desaparece y se puede prometer sin problema, así que conviene
  dejar el hueco preparado en la maqueta. Y en ningún caso se menciona el
  comportamiento actual.
- **El agente no vive dentro de este panel.** La conversación la genera un
  motor externo, n8n con un modelo de lenguaje; lo que se ve aquí es su estado,
  su historial y sus controles. No lo presentes como si el modelo corriese
  dentro del producto.
- **No hay app móvil.** El panel es adaptable y funciona bien en el móvil del
  navegador, pero no existe aplicación nativa.
- **No hay borrado automático por caducidad.** El plazo de conservación de la
  historia clínica es configurable y se calcula, pero nada purga solo: depende de
  normativa autonómica todavía por confirmar.
- **No hay devoluciones de cobros.** Un recibo no se deshace; está previsto como
  documento propio, pero aún no existe.
- **Cuidado con los porcentajes.** Las cifras de la página actual, como la
  reducción de ausencias o el tiempo de respuesta, son promesas comerciales, no
  medidas del producto. Si las mantienes, no las mezcles en la misma sección que
  las descripciones funcionales de estas capturas, que sí son verificables.

---

## 7. Nota sobre las capturas

Los datos que aparecen en ellas son de una **clínica de demostración** sembrada a
propósito: pacientes, citas, facturas y conversaciones inventadas. No hay ni un
dato real de ningún paciente. Si en algún momento alguien pregunta, se puede
decir sin problema.

Los nombres que aparecen en las capturas son ficticios y la clínica se llama
Clínica Podológica Propus, con dirección en Málaga. Si prefieres un nombre
neutro en la web, conviene rehacer las capturas antes que retocarlas.
