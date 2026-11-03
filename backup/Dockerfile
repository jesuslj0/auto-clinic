# Copias de seguridad de PostgreSQL hacia Backblaze B2.
#
# Parte de la imagen de Postgres 16 (la misma versión que `db`) para que
# `pg_dump` y el servidor coincidan: un pg_dump más antiguo que el servidor se
# niega a volcar. Se construye en GitHub Actions como las otras dos imágenes.
FROM postgres:16-alpine

RUN apk add --no-cache rclone age tzdata

COPY backup.sh run.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/run.sh

# rclone se configura solo con variables de entorno; necesita un sitio donde
# escribir aunque no guarde nada.
ENV RCLONE_CONFIG=/tmp/rclone.conf

USER postgres
WORKDIR /tmp

# Sustituye al entrypoint de la imagen de Postgres: aquí no se arranca ningún servidor.
ENTRYPOINT ["/usr/local/bin/run.sh"]
