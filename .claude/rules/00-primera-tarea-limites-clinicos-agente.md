# REGLA OBLIGATORIA Y PRIMERA TAREA — límites clínicos del agente

**Estado: PENDIENTE** (anotada el 2026-11-25 por Xexu). Quien abra este repo para trabajar en AutoClinic
(Xexu, Jesús o un agente que trabaje por ellos) **debe hacer esto ANTES de cualquier otra tarea**:

1. **Antes de nada, recuérdaselo al usuario** en tu primer mensaje de la sesión («hay una tarea obligatoria
   pendiente: los límites clínicos del agente») y propón hacerla la primera.
2. `git pull` (puede haber cambios nuevos; no la hagas sobre una copia vieja).
3. Comprueba que sigue pendiente: si `agent/persona.py` ya contiene `CLINICAL_BOUNDARY_LINE` y existe
   `tests/agent/test_persona_limites_clinicos.py`, está hecha: cambia arriba el estado a **HECHA** (con fecha
   y commit) y sigue con lo que toque.
4. Solo si el usuario dice expresamente que no toca hoy, se aplaza; en ese caso díselo otra vez al empezar
   la siguiente sesión.

## Por qué es obligatoria

A las clínicas les prometemos (web y contrato) que **AutoClinic es una herramienta de gestión: citas,
avisos y ficha. No diagnostica, no recomienda tratamientos y no sustituye el criterio del profesional;
toda decisión clínica es del profesional sanitario.** El producto tiene que cumplirlo de verdad, y hoy no
lo hace:

- El prompt del agente **no tiene ninguna regla que le impida dar consejo clínico** (solo un freno parcial
  ante fotos: «no valores ni diagnostiques»).
- **No hay ninguna regla de urgencias.**
- `SERVICES_LINE` (`agent/persona.py`) manda preguntar «qué le pasa» y «buscar tú el servicio que encaja»:
  es una zona gris, orienta al paciente sobre su problema sin llegar a diagnosticar.

No se puede vender ni firmar con clínicas hasta que el comportamiento coincida con lo prometido.

## Qué hay que hacer

### 1. `agent/persona.py` (donde se redacta el prompt, y donde se prueba con pytest)

- **Añadir `CLINICAL_BOUNDARY_LINE`** y meterla en `build_persona_prompt` justo después de `SERVICES_LINE`
  y antes de `style_notes` y `CLOSING_LINE`. Tiene que ir **siempre**, con perfil o sin él, con cualquier
  tono, y **nada que escriba la clínica en `style_notes` puede anularla** (las notas de estilo van después
  y `CLOSING_LINE` ya dice que no anulan reglas). Texto propuesto (revisarlo, no copiarlo a ciegas):

  > LÍMITES CLÍNICOS (OBLIGATORIO): eres una herramienta de gestión de la clínica (citas, avisos e
  > información práctica), no personal sanitario. NUNCA diagnostiques; no valores síntomas, lesiones, fotos
  > ni resultados; no recomiendes, desaconsejes ni ajustes tratamientos, medicación, dosis, curas ni
  > cuidados; no digas si algo «es grave», «es normal» o «puede esperar», ni sustituyas el criterio del
  > profesional. Si el paciente pregunta algo clínico, dile con amabilidad que eso solo puede valorarlo el
  > profesional de la clínica y ofrécele pedir cita o dejar el recado para que lo atienda el equipo. Sí
  > puedes dar información práctica y no clínica que conste en la información de la clínica (horarios,
  > precios, qué incluye cada servicio tal como lo describe la clínica, cómo prepararse para la cita si la
  > clínica lo ha indicado).
  >
  > URGENCIAS: si el paciente cuenta algo que parezca urgente (dolor muy intenso, sangrado que no para,
  > fiebre con herida, signos de infección que se extiende, dificultad para respirar, pérdida de
  > conocimiento) o dice que es una urgencia, no lo valores ni lo trates como una cita normal: dile que si
  > es una emergencia llame al 112 y que, si no, contacte con la clínica por teléfono cuanto antes; avisa
  > al equipo por el mecanismo que ya exista (revisar `test_human_mode.py` y el modo humano) para que lo
  > vea una persona.

- **Reformular `SERVICES_LINE`**: quitar «qué le pasa» y «busca tú el servicio que encaja». La idea: preguntar
  qué **servicio** quiere o el **motivo de la cita con sus propias palabras**, sin interpretarlo; si no
  sabe qué pedir, ofrecer los servicios que la clínica tiene y dejar que decida él o que lo valore el
  profesional en la cita, **sin decirle qué tiene ni qué necesita**. El resto de la línea (no enumerar los
  servicios por iniciativa propia, darlos si los pide) se queda como está.

### 2. Pruebas pytest (`tests/agent/test_persona_limites_clinicos.py`, nuevo)

- El prompt contiene el bloque de límites clínicos y el de urgencias con y sin perfil guardado, y con los
  tres tonos.
- El bloque aparece **antes** de `style_notes` y `build_persona_prompt(...)` sigue acabando en
  `CLOSING_LINE` (ya lo comprueba `test_agent_profile.py`; que siga verde).
- Unas `style_notes` que digan «da consejo médico» o «ignora las reglas anteriores» no quitan el bloque.
- `SERVICES_LINE` ya no contiene «qué le pasa» ni «el servicio que encaja».

### 3. Lo que NO está en este repo y hay que hacer igual

- **El prompt de n8n** (`Agente IA Clinica` / `WA-Inbound-Orchestrator`) lleva la misma frase de «qué le pasa
  / busca el servicio que encaja» y tampoco tiene la regla. Lo toca Xexu o Jesús a mano en n8n (Claude no
  tiene permiso sobre esos ficheros). Dejarlo igual que `persona.py`.
- Si el prompt de n8n sigue teniendo reglas propias que chocan con las nuevas, **mandan las nuevas**.

## Cuando esté hecha

- Documentarlo en `CLAUDE.md` (sección del agente / personalidad) en dos líneas.
- Cambiar el **Estado** de este fichero a HECHA (fecha y commit) o borrarlo si ya no hace falta.
- Avisar a Xexu: lo tiene anotado en el contrato interno de AutoClinic como condición para firmar.

## Límites de esta tarea

- No es una tarea de diseño: no cambiar la personalidad configurable, los tonos, la bienvenida ni el aviso de IA
  y privacidad (ya están bien y tienen sus pruebas).
- No inventar números de teléfono ni enlaces de urgencias: el 112 es el único dato fijo.
