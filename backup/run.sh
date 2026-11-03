#!/bin/sh
# Bucle del contenedor: espera a la hora de la copia, la hace y vuelve a esperar.
# Un fallo no tumba el contenedor (backup.sh ya lo registra y avisa); se
# reintenta al día siguiente. Sin cron para no perder el entorno ni la zona horaria.
#
# BACKUP_TIME      HH:MM en la hora de TZ (por defecto 02:30; el purgado de chats va a las 03:30)
# BACKUP_ON_START  true = hace una copia nada más arrancar (útil para la primera prueba)
set -eu

TIME="${BACKUP_TIME:-02:30}"
# «08» en aritmética de sh se lee como octal (y falla): se le quita el cero inicial.
num() { n="${1#0}"; echo "${n:-0}"; }
hh="$(num "${TIME%%:*}")"
mm="$(num "${TIME##*:}")"
target=$(( hh * 3600 + mm * 60 ))

if [ "${BACKUP_ON_START:-false}" = "true" ]; then
    /usr/local/bin/backup.sh || true
fi

while true; do
    now=$(( $(num "$(date +%H)") * 3600 + $(num "$(date +%M)") * 60 + $(num "$(date +%S)") ))
    wait=$(( (target - now + 86400) % 86400 ))
    # Recién hecha la copia, now == target + unos segundos: no repetir en el acto.
    [ "$wait" -lt 60 ] && wait=$(( wait + 86400 ))
    echo "[backup] próxima copia en ${wait} s (${TIME} ${TZ:-UTC})"
    sleep "$wait"
    /usr/local/bin/backup.sh || true
done
