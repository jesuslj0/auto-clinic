#!/bin/sh
# Una copia: pg_dump → comprobación → cifrado con age → subida a Backblaze B2.
#
# Los datos son de salud (RGPD art. 9): el volcado se cifra ANTES de salir del
# servidor con una clave PÚBLICA de age. Aquí solo vive la pública; la privada
# la guarda quien restaura, fuera del servidor, así que quien robe el bucket o
# el servidor no puede leer las copias.
#
# Variables (obligatorias): PGHOST PGUSER PGPASSWORD PGDATABASE,
#   B2_KEY_ID B2_APPLICATION_KEY B2_BUCKET BACKUP_AGE_RECIPIENT
# Opcionales: BACKUP_PREFIX (db), BACKUP_MONTHLY_PREFIX (monthly),
#   BACKUP_PING_URL (healthchecks.io o similar: avisa si deja de llegar el ping)
set -eu

: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}" "${PGDATABASE:?}"
: "${B2_KEY_ID:?}" "${B2_APPLICATION_KEY:?}" "${B2_BUCKET:?}"
: "${BACKUP_AGE_RECIPIENT:?}"

PREFIX="${BACKUP_PREFIX:-db}"
MONTHLY_PREFIX="${BACKUP_MONTHLY_PREFIX:-monthly}"
PING="${BACKUP_PING_URL:-}"

export RCLONE_CONFIG_B2_TYPE=b2
export RCLONE_CONFIG_B2_ACCOUNT="$B2_KEY_ID"
export RCLONE_CONFIG_B2_KEY="$B2_APPLICATION_KEY"
# La clave está limitada al bucket: rclone no debe intentar crearlo.
export RCLONE_B2_NO_CHECK_BUCKET=true

log() { echo "[backup] $(date -u +%FT%TZ) $*"; }

ping_url() {
    [ -n "$PING" ] || return 0
    wget -q -T 10 -O /dev/null "$PING$1" 2>/dev/null || log "no se pudo avisar a $PING$1"
}

work="$(mktemp -d)"
cleanup() {
    status=$?
    rm -rf "$work"
    if [ "$status" -ne 0 ]; then
        log "FALLO (código $status)"
        ping_url "/fail"
    fi
}
trap cleanup EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="${PGDATABASE}-${stamp}.dump.age"
year="$(date -u +%Y)"
month="$(date -u +%m)"

log "volcando ${PGDATABASE}"
ping_url "/start"
# Formato custom (-Fc): ya viene comprimido y permite restaurar tablas sueltas.
# Sin owner ni ACL para poder restaurarlo con otro usuario.
pg_dump -Fc --no-owner --no-acl -f "$work/db.dump"

# Un volcado que no se puede listar no es una copia. Falla aquí, no el día que haga falta.
pg_restore -l "$work/db.dump" >/dev/null
log "volcado válido ($(wc -c <"$work/db.dump") bytes)"

age -r "$BACKUP_AGE_RECIPIENT" -o "$work/$name" "$work/db.dump"
rm -f "$work/db.dump"
size="$(wc -c <"$work/$name")"

upload() {
    dest="$1"
    rclone copyto "$work/$name" "b2:${B2_BUCKET}/${dest}" --b2-chunk-size 16M
    # copyto compara el SHA-1 al subir; esto además confirma que el objeto existe y mide lo mismo.
    remote_size="$(rclone lsf --format s "b2:${B2_BUCKET}/${dest}")"
    if [ "$remote_size" != "$size" ]; then
        log "el tamaño en B2 ($remote_size) no coincide con el local ($size)"
        return 1
    fi
    log "subida ${dest} (${size} bytes)"
}

upload "${PREFIX}/${year}/${month}/${name}"
# El día 1 se guarda además una copia mensual, con su propia regla de caducidad en B2.
if [ "$(date -u +%d)" = "01" ]; then
    upload "${MONTHLY_PREFIX}/${year}/${month}/${name}"
fi

ping_url ""
log "copia terminada"
