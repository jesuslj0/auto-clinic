# Copias de seguridad de la base de datos (PostgreSQL → Backblaze B2)

Servicio `backup` de `docker-compose.prod.yml`. Cada noche (02:30, hora de Madrid):

1. `pg_dump -Fc` de la base de datos y comprobación con `pg_restore -l`
   (un volcado que no se puede listar no es una copia: falla ahí).
2. **Cifrado con [age](https://age-encryption.org) antes de salir del servidor.** En el
   servidor solo está la clave *pública*; la privada la guarda quien restaura, fuera del
   servidor. Quien se lleve el bucket o el servidor no puede leer las copias (son datos de
   salud, RGPD art. 9).
3. Subida a B2 (`db/AAAA/MM/…dump.age`), con comprobación de SHA-1 y de tamaño. El día 1
   de cada mes se sube además a `monthly/`.
4. Si `BACKUP_PING_URL` está definida, avisa al empezar, al acabar bien (`/`) y al fallar
   (`/fail`). Úsalo con healthchecks.io (o similar): **el aviso que importa es el que
   deja de llegar.**

La retención no la hace el servidor sino B2 (reglas de ciclo de vida, abajo). Así la clave
del servidor no necesita poder borrar nada.

## Puesta en marcha (una sola vez)

### 1. Región de la cuenta

En B2 la región se elige al crear la **cuenta**, no el bucket. Para datos de salud conviene
**EU Central (Ámsterdam)**: en *Buckets* mira el endpoint (`s3.eu-central-003…`). Si tu cuenta
quedó en US, crea una cuenta nueva en la UE antes de subir nada.

### 2. Crear el bucket

*Buckets → Create a Bucket*

- **Nombre**: único en todo B2, p. ej. `autoclinic-db-<algo-aleatorio>`.
- **Files in Bucket are**: **Private**.
- **Default Encryption**: Enable (SSE-B2). Es una capa más; la que protege de verdad es age.
- **Object Lock**: recomendable activarlo (no se puede quitar después). Hace las copias
  inmutables durante el periodo que fijes (p. ej. 7–14 días), así que ni un atacante con la
  clave del servidor ni un error nuestro pueden borrarlas. Con Object Lock, la regla de
  caducidad de abajo solo actúa pasado ese plazo.

### 3. Reglas de ciclo de vida (retención)

*Bucket Settings → Lifecycle Settings → Use custom lifecycle rules*. Dos reglas:

| File name prefix | Days till hide | Days till delete (tras ocultar) | Resultado |
|---|---|---|---|
| `db/` | 30 | 1 | 30 copias diarias |
| `monthly/` | 365 | 1 | 12 copias mensuales |

Ajusta los plazos a lo que decida la clínica; recuerda que una copia contiene los datos tal
como estaban ese día, incluidos los que el purgado de chats borra después
(`conversation_retention_months`). Que el aviso de privacidad no prometa menos de lo real.

### 4. Clave de aplicación para el servidor

*App Keys → Add a New Application Key* permite «Read and Write», que incluye **borrar**.
Mejor crearla por CLI sin permiso de borrado ni de lectura:

```bash
pip install b2            # o el binario oficial
b2 account authorize      # con tu master key, solo en tu máquina
b2 key create --bucket <NOMBRE_BUCKET> autoclinic-backup-server listBuckets,listFiles,writeFiles
```

Imprime `keyID applicationKey` **una sola vez**. Si no quieres la CLI, usa en la web
«Read and Write» limitada a este bucket, y entonces Object Lock (paso 2) es imprescindible.
La master key no se pone nunca en el servidor.

### 5. Par de claves de cifrado (age)

En tu máquina (no en el servidor):

```bash
age-keygen -o autoclinic-backup.key     # imprime «Public key: age1…»
```

- La **pública** (`age1…`) va al servidor como `BACKUP_AGE_RECIPIENT`.
- El fichero **`autoclinic-backup.key` es la única forma de abrir las copias.** Guárdalo en
  el gestor de contraseñas y en una copia impresa o USB fuera de casa. Si se pierde, todas
  las copias son basura; si se filtra, cualquiera con acceso al bucket las lee.

### 6. Variables en Coolify (runtime)

```
B2_KEY_ID=            # del paso 4
B2_APPLICATION_KEY=   # del paso 4
B2_BUCKET=            # nombre del bucket
BACKUP_AGE_RECIPIENT= # age1… (paso 5)
BACKUP_PING_URL=      # opcional: URL del check de healthchecks.io (sin /fail ni /start)
BACKUP_TIME=02:30     # opcional
BACKUP_ON_START=true  # solo para la primera prueba; quitar después
```

### 7. Desplegar y probar

`git merge main` en `prod` y push: el workflow construye también `auto-clinic-backup`
(el paquete nace privado en GHCR; el `docker login` del servidor ya vale). Con
`BACKUP_ON_START=true` verás en los logs del contenedor `backup`:

```
[backup] … volcado válido (… bytes)
[backup] … subida db/2026/11/….dump.age
[backup] … copia terminada
```

y el fichero en *Browse Files* del bucket. Después **quita `BACKUP_ON_START`**.

## Restaurar

Todo se hace en tu máquina, nunca descifrando en el servidor. Hacen falta `rclone`, `age`,
`pg_restore` (cliente 16), la clave privada `autoclinic-backup.key` y una clave de B2
**de lectura**, distinta de la del servidor y guardada fuera de Coolify:

```bash
b2 key create --bucket <BUCKET> autoclinic-restore listBuckets,listFiles,readFiles
```

(En la web vale «Read Only» limitada al bucket.) Descargar y descifrar:

```bash
export RCLONE_CONFIG_B2_TYPE=b2
export RCLONE_CONFIG_B2_ACCOUNT=<keyID de lectura>
export RCLONE_CONFIG_B2_KEY=<applicationKey de lectura>
export RCLONE_B2_NO_CHECK_BUCKET=true

rclone lsf -R b2:<BUCKET>/db | sort | tail -5         # elegir copia
rclone copyto b2:<BUCKET>/db/2026/11/clinic-20261103T013000Z.dump.age copia.dump.age
age -d -i ~/secretos/autoclinic-backup.key copia.dump.age > copia.dump
pg_restore -l copia.dump | head                       # ver que es legible
```

### Caso 1 · Prueba de restauración (tras la puesta en marcha y cada trimestre)

Cargar la copia en un Postgres desechable; no toca producción.

```bash
docker run -d --name pg-prueba -e POSTGRES_PASSWORD=x -e POSTGRES_DB=clinic_restaurada postgres:16-alpine
sleep 5
docker cp copia.dump pg-prueba:/tmp/copia.dump
docker exec pg-prueba pg_restore -U postgres -d clinic_restaurada --no-owner /tmp/copia.dump
docker exec pg-prueba psql -U postgres -d clinic_restaurada \
  -c "select count(*) from patients_patient; select count(*) from clinical_note;"
docker rm -fv pg-prueba    # -v borra también el volumen con los datos
```

Los recuentos deben coincidir con lo esperado. **Una copia que nunca se ha restaurado no es
una copia.**

### Caso 2 · Pérdida parcial (se borró un paciente, una nota, una cita…)

**No restaurar sobre producción.** Primero mirar si el dato sigue ahí con `deleted_at`
relleno (casi todo es borrado lógico). Si no, hacer el Caso 1 con una copia anterior al
problema, localizar las filas en esa base temporal y recuperarlas a mano en producción.
Así no se pierde lo hecho desde la copia.

### Caso 3 · Desastre (base corrupta o borrada, servidor perdido)

Es el único que pisa producción y pierde lo escrito desde la última copia (hasta 24 h).

1. En Coolify, **parar** `web`, `celery`, `celery-beat` y `backup`. Dejar `db` en marcha.
   Si el servidor se perdió, levantar el compose en uno nuevo con las mismas variables.
2. **Guardar el estado actual**, aunque esté dañado (en el servidor, como root):
   `docker exec <contenedor_db> pg_dump -U <POSTGRES_USER> -Fc <POSTGRES_DB> > /root/antes_de_restaurar.dump`
3. Subir `copia.dump` al servidor (`scp`).
4. Vaciar y restaurar:
   ```bash
   docker cp /root/copia.dump <contenedor_db>:/tmp/copia.dump
   docker exec <contenedor_db> psql -U <POSTGRES_USER> -d <POSTGRES_DB> \
     -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
   docker exec <contenedor_db> pg_restore -U <POSTGRES_USER> -d <POSTGRES_DB> --no-owner /tmp/copia.dump
   docker exec <contenedor_db> rm /tmp/copia.dump
   ```
   Borrar también `/root/copia.dump` del servidor: está sin cifrar.
5. Arrancar `web` (su comando ya ejecuta `migrate`). Comprobar pacientes y notas; el trigger
   que blinda las notas firmadas (`clinical/migrations/0002`) viaja en el volcado.
6. Arrancar `celery`. **`celery-beat` el último**, tras revisar las citas de las próximas
   24 h: al volver atrás también retroceden las marcas de «recordatorio enviado»
   (`reminder_24h_sent`) y los `event_id` de los avisos de cita, y los WhatsApp podrían
   reenviarse a pacientes que ya los recibieron.
7. Arrancar `backup` y forzar una copia nueva (`BACKUP_ON_START=true` una vez).

### Lo que la copia no trae

- `SECRET_KEY` y `TWO_FACTOR_ENCRYPTION_KEY` viven en Coolify, no en la base. Sin la
  segunda, los secretos de 2FA guardados son ilegibles y habría que resetear a todos.
  Guardar copia de esas variables en el gestor de contraseñas.
- Lo escrito entre la última copia y el incidente. Para reducir ese margen, acortar el
  intervalo en `run.sh` (p. ej. cada 6 h); para minutos haría falta archivado WAL.

## Lo que NO cubre

- Las **fotos clínicas, firmas de consentimiento y adjuntos de WhatsApp** viven en el
  bucket privado de Cloudflare R2, no en PostgreSQL. Esta copia no las incluye. Hay que
  decidir cómo respaldarlas (p. ej. `rclone sync` de R2 a otro bucket de B2 con la misma
  idea de clave sin borrado).
- El volumen `media_files` (logos de clínica): irrelevante, se resube.
- Los workflows de n8n: se exportan aparte.
