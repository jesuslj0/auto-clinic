# `exports` — exportación de datos

Saca los datos de la aplicación en un paquete que se pueda guardar o llevar a otro
programa **sin perder nada**: la copia que un paciente pide de su ficha (RGPD arts. 15
y 20, Ley 41/2002 art. 18) y el traspaso de toda la clínica (cambio de software, cierre).

Todo es de panel: sesión, solo administradores de clínica, **sin API REST** (el agente no
llega) y con `AccessLog` en cada petición y cada descarga. El ZIP vive solo
`EXPORT_RETENTION_DAYS` (7) días en el bucket privado.

## Qué sale

Un ZIP con:

```
LEEME.txt              qué es, cómo se organiza, qué se dejó fuera y por qué
manifest.json          filas y SHA-256 de cada fichero; modelos y campos excluidos
manifest_ficheros.jsonl un registro por adjunto: ruta, huella, huella guardada, estado
verificar_firmas.py    comprueba el paquete sin la aplicación (solo biblioteca estándar)
paciente.pdf           solo en la ficha de un paciente: la copia legible
datos/<entidad>.jsonl  una entidad por fichero, relaciones por id
adjuntos/<tipo>/…      fotos, firmas y audios originales
```

Dos ámbitos con el mismo motor (`builder.build_export_zip(Scope(clinic, patient=None))`):

- **Paciente** (`Scope(clinic, patient)`): su historia, adjuntos, citas, facturas y chats, más el
  PDF. De terceros (contactos responsables, profesionales) solo lo mínimo. Sin auditoría.
- **Clínica** (`Scope(clinic)`): todos los pacientes, más catálogos, personal, plantillas,
  numeraciones y auditoría. Sin PDF.

## Reglas que no se rompen

- **El contenido se decide en un solo sitio**: `schema.py`. Cada entidad lista de forma explícita los
  campos que salen y, aparte, los que no con su motivo. `tests/exports/test_schema_guard.py`
  falla si un modelo exportado gana un campo que nadie ha decidido: una exportación «completa» no
  puede dejar de serlo en silencio.
- **Se lee todo, también lo borrado.** `all_objects` y `deleted_at` en la fila. Los `objects` de
  `Lesion`, `LesionObservation`, `ClinicalAlert`, `PatientInvoice` y `Payment` ocultan borrados.
- **Se filtra por `patient_id`/`clinic_id`**, no recorriendo `patient.<relación>`: la FK de la capa
  clínica y de facturación es `DO_NOTHING` sin constraint y puede apuntar a un paciente que ya no existe.
- **Se exporta lo congelado** (`frozen_*`, `text_copy`, `snapshot`, `lines`), nunca lo recalculado.
- **Los secretos no salen**: claves de API, tokens, contraseñas, TOTP, token de cita y de enlace de ficha.
  Un test lo comprueba con valores reales.
- **El sello de las notas firmadas se puede verificar fuera**: `verificar_firmas.py` repite el cálculo de
  `clinical/hashing.py`. Hay un test que exige que den lo mismo (incluidas notas sin los campos «posteriores»).
  Si cambia el JSON canónico de la nota, hay que cambiar también el script.
- **No hay memoria de más**: `.iterator()`, adjuntos en bloques de 64 KB, ZIP a disco. La máquina comparte
  host con n8n.
- **Un adjunto que falta no tumba la exportación**: se anota como `missing`/`unreadable` en el manifest y
  se cuenta. Un error a mitad de lectura sí la hace fallar (un ZIP con un adjunto cortado sería peor).
- **El PDF no sale a la red**: plantilla autocontenida y `URLFetcher(allowed_protocols=('data',),
  fail_on_errors=True)`. Un recurso externo hace fallar la exportación. Si el PDF falla, la exportación
  falla: no se entrega un ZIP de paciente sin su copia legible.
  (`FatalURLFetchingError` hereda de `BaseException`: `pdf.py` lo envuelve en `RuntimeError`.)

## Flujo

`views.PatientExportCreateView` / `ClinicExportCreateView` → `service.request_export()` (fila `pending`, una
activa por paciente y una por clínica por constraint parcial, encola con `on_commit`) →
`tasks.build_export` → `generation.generate()` (`running`, ZIP en disco, subida con clave UUID bajo
`exports/`, `ready` con tamaño y SHA-256) → notificación al solicitante (`EXPORT_READY`, genérica: el push no
lleva nombres) → `ExportDownloadView` (404 si no está lista o caducó; redirige a URL firmada).

Exportar la clínica pide contraseña + código 2FA (`core.reauth`, mismo bloqueo que el login) y **exige tener
el 2FA activo**. Cabeceras `Referrer-Policy: same-origin`, nunca `no-referrer`.

`retention.purge_expired_exports` (beat, 04:00) borra el objeto de los caducados (la fila queda `expired`) y
da por fallidas las que se quedaron `pending`/`running` más de 2 h.

## Fuera de alcance (pendiente)

CSV para Excel, FHIR, importador y nombre de fichero amigable en la descarga.
