# Tarea: app de auditoría (escrituras y lecturas)

## Contexto

Este repositorio es un CRM para clínicas de podología construido con Django. Va a
almacenar datos de salud, que bajo el RGPD son categoría especial (art. 9) y bajo la
Ley 41/2002 forman parte de la historia clínica.

Todavía **no existe** la capa clínica (historia, episodios, visitas, notas, lesiones).
Se va a construir después. Esta tarea prepara los cimientos de auditoría para que
cada modelo clínico nazca ya cubierto.

Actualmente el proyecto tiene la capa administrativa: `Paciente`, `Profesional`,
`Servicio`, `Cita`, `Agenda` (tramos horarios, bajas), y un historial de estados de
`Cita`. Ese historial es un log de dominio de un modelo concreto; lo que se pide aquí
es una infraestructura genérica y distinta. No lo sustituyas ni lo migres.

Existe además un agente de n8n que consume la API para gestionar citas. Sus llamadas
también deben quedar auditadas.

## Antes de escribir código

1. Explora el repo y resume qué encuentras: layout de apps, modelo de usuario,
   si hay DRF, si el proyecto es WSGI o ASGI, convenciones de tests, cómo se
   nombran las apps, cómo está resuelto el historial de estados de `Cita`.
2. Propón un plan por escrito con los ficheros que vas a crear o tocar.
3. **Espera mi visto bueno antes de implementar.**
4. Señala cualquier decisión donde veas más de una opción razonable en vez de
   elegir por tu cuenta.

## Objetivo

Una app nueva, `auditoria`, con dos registros independientes:

- **`RegistroCambio`** — toda escritura (alta, modificación, baja) sobre modelos
  registrados.
- **`RegistroAcceso`** — toda lectura de datos clínicos o personales sensibles.

Son dos cosas distintas y no deben compartir tabla. Las lecturas no generan señales
de Django, así que se instrumentan en la capa de vistas.

## Requisitos comunes a ambos modelos

- **Solo inserción.** Sobrescribe el manager para que `update()` y `delete()` de
  queryset lancen excepción, y para que `save()` sobre una instancia ya persistida
  también lance.
- Sin `on_delete=CASCADE` hacia ellos desde ningún sitio.
- Registrados en el admin en **modo solo lectura**: `has_add_permission`,
  `has_change_permission` y `has_delete_permission` devolviendo `False`, con
  filtros y búsqueda útiles.
- Nunca deben auditarse a sí mismos (evita recursión infinita).
- Campos denormalizados de usuario: además del FK, guarda `usuario_repr` con el
  nombre o email en el momento del evento, para que el log siga siendo legible si
  el usuario se da de baja. El FK debe ser `SET_NULL`, nunca `CASCADE`.
- Índices pensados para las dos consultas reales: "todo lo ocurrido sobre el
  paciente X" y "todo lo que hizo el usuario Y en un rango de fechas".

## `RegistroCambio`

Campos mínimos:

- `timestamp` (indexado)
- `usuario` (FK nullable, `SET_NULL`) y `usuario_repr`
- `content_type` + `object_id` (GenericForeignKey) y `object_repr`
- `paciente` (FK nullable al modelo de paciente, `SET_NULL`) — resuelto mediante
  una función configurable, porque no todos los modelos auditados cuelgan de un
  paciente directamente
- `accion`: `crear` / `modificar` / `eliminar`
- `cambios`: `JSONField` con la forma `{campo: {"antes": ..., "despues": ...}}`
- `ip`, `user_agent`
- `origen`: `web` / `api` / `admin` / `comando` / `n8n`

Implementación:

- Señales `post_save` y `post_delete` sobre los modelos registrados, más
  `pre_save` para capturar el estado anterior y poder calcular el diff.
- Un **registro explícito de modelos auditados**, estilo
  `auditoria.registry.registrar(Modelo, excluir=[...], sensibles=[...])`, invocado
  desde el `AppConfig.ready()` de cada app. Nada de auditar todo automáticamente.
- Los campos marcados como `sensibles` deben guardar **solo el nombre del campo
  que cambió, no los valores**. El log de auditoría no puede convertirse en una
  segunda copia sin cifrar de la historia clínica.
- Excluye por defecto campos de ruido: `updated_at`, `last_login`, y similares.

**Limitación conocida que debes documentar y avisar en el código:** `bulk_create`,
`queryset.update()` y `queryset.delete()` no disparan señales. Documenta esto en el
README de la app y añade un aviso claro sobre no usar esas operaciones sobre
modelos auditados.

## `RegistroAcceso`

Campos mínimos:

- `timestamp` (indexado)
- `usuario` (FK nullable) y `usuario_repr`
- `paciente` (FK nullable) — el paciente cuyos datos se han consultado
- `content_type` + `object_id` nullable (los listados no apuntan a un objeto)
- `accion`: `ver` / `listar` / `buscar` / `exportar` / `imprimir` / `descargar_adjunto`
- `ruta` (path de la petición), `metodo` (verbo HTTP)
- `num_resultados` (nullable, para listados y búsquedas)
- `ip`, `user_agent`, `origen`
- `motivo` (texto nullable) — reservado para accesos excepcionales que en el futuro
  requieran justificación

Implementación:

- Un `RegistroAccesoMixin` para CBVs y un decorador equivalente para FBVs.
- Si el proyecto usa DRF, además una clase base o mixin para viewsets.
- Una función de bajo nivel `registrar_acceso(...)` invocable desde cualquier sitio,
  para la vista que servirá adjuntos protegidos más adelante.
- **No instrumentes por middleware global.** Generaría ruido masivo sobre
  estáticos, salud del servicio y vistas administrativas, y el log dejaría de ser
  útil. La instrumentación es explícita y por vista.

## Contexto de petición

Necesitas usuario, IP y user agent dentro de las señales, donde no hay `request`.

- Usa **`contextvars`, no `threading.local`**, para que funcione bajo ASGI.
- Un middleware ligero que puebla el contexto al inicio de la petición y lo limpia
  al final, incluso si hay excepción.
- Fuera de petición (comandos de gestión, shell, tareas programadas) el registro
  debe seguir creándose con `usuario` nulo y `origen` adecuado, sin romper.
- Para las llamadas del agente de n8n, detecta el origen por el token o cabecera
  que ya use el proyecto y márcalo como `n8n`.

## Política ante fallo

Si la escritura del registro de auditoría falla, hay dos opciones y quiero decidirla
yo: abortar la operación (fail-closed, más seguro legalmente) o dejarla pasar
registrando el fallo (fail-open, mejor disponibilidad). Impleméntalo como setting
configurable, plantéame la pregunta en el plan y propón un valor por defecto
razonado.

## Tests

Siguiendo las convenciones que ya haya en el repo:

- Se crea `RegistroCambio` al crear, modificar y borrar un modelo registrado.
- El diff contiene solo los campos que cambiaron realmente.
- Los campos marcados como sensibles no filtran valores al log.
- Un modelo no registrado no genera ruido.
- `RegistroCambio` y `RegistroAcceso` no se pueden modificar ni borrar: verifica
  que se lanza la excepción esperada.
- El mixin de lectura crea el registro con el paciente correcto.
- El contexto de petición se limpia entre peticiones y no se filtra entre usuarios.
- Funciona sin `request` (ejecución desde comando de gestión).

## Entregables

- App `auditoria` con modelos, managers, señales, registry, middleware, mixins,
  admin y migraciones.
- Tests.
- `README.md` dentro de la app explicando cómo registrar un modelo nuevo, cómo
  instrumentar una vista, la limitación de las operaciones en bloque y la política
  de retención pendiente de fijar.
- Actualización de `CLAUDE.md` con una nota breve: todo modelo clínico futuro debe
  registrarse en auditoría y toda vista que exponga datos clínicos debe instrumentar
  la lectura.

## Fuera de alcance

- No crees modelos clínicos.
- No toques `Paciente`, `Profesional`, `Servicio`, `Cita` ni `Agenda` más allá de
  registrarlos en auditoría.
- No modifiques el historial de estados de `Cita`.
- No añadas dependencias nuevas sin preguntarme antes. Si crees que
  `django-simple-history` u otra librería resuelve mejor una parte, dímelo con
  los pros y contras en lugar de instalarla.
- No ejecutes migraciones contra ninguna base de datos que no sea local.
- Traduce los campos y nombres de modelos que te he dado a inglés. 