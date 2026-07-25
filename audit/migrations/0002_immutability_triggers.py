"""Inmutabilidad por debajo del ORM: trigger BEFORE UPDATE OR DELETE.

La capa ORM (`AppendOnlyModel`) cierra las puertas de Django, pero no protege
frente a un `UPDATE`/`DELETE` en SQL crudo ni frente a quien entre por `psql`.
Esta migración instala la segunda barrera, en la propia base de datos:

- Un `UPDATE` sobre cualquiera de las dos tablas de auditoría falla SIEMPRE.
- Un `DELETE` falla también, SALVO que la conexión traiga puesta la válvula
  `audit.purga = 'on'`. Esa válvula la pone —y solo dentro de su transacción—
  el comando `purge_audit_logs`, que aplica la política de retención en Python.

El trigger no sabe nada de plazos de retención: distingue únicamente «purga
autorizada sí/no». El «qué se puede borrar» (por clínica y por tipo de log) vive
en el comando de gestión.
"""
from django.db import migrations


# La función es idempotente por sí sola (CREATE OR REPLACE). Los triggers no lo
# son (CREATE TRIGGER falla si ya existe), así que se les antepone un DROP IF
# EXISTS para que la migración pueda re-aplicarse sin reventar.
FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION audit_prevent_modification()
RETURNS TRIGGER AS $$
BEGIN
    -- DELETE: permitido solo con la válvula puesta. current_setting(..., true)
    -- lleva el segundo argumento a propósito: si la variable no está definida
    -- devuelve NULL en vez de lanzar error, de modo que la comparación falla y
    -- caemos en el RAISE.
    IF TG_OP = 'DELETE' THEN
        IF current_setting('audit.purga', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION
            'audit: DELETE no autorizado sobre % (registro de solo inserción; '
            'falta la valvula audit.purga)', TG_TABLE_NAME
            USING ERRCODE = 'raise_exception';
    END IF;

    -- Cualquier otra operación (en la práctica, UPDATE) se rechaza sin
    -- excepciones: la válvula es SOLO para DELETE, un registro de auditoría no
    -- se modifica jamás.
    RAISE EXCEPTION
        'audit: % no permitido sobre % (registro de solo insercion)',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_change_log_no_modify ON audit_change_log;
CREATE TRIGGER audit_change_log_no_modify
    BEFORE UPDATE OR DELETE ON audit_change_log
    FOR EACH ROW EXECUTE FUNCTION audit_prevent_modification();

DROP TRIGGER IF EXISTS audit_access_log_no_modify ON audit_access_log;
CREATE TRIGGER audit_access_log_no_modify
    BEFORE UPDATE OR DELETE ON audit_access_log
    FOR EACH ROW EXECUTE FUNCTION audit_prevent_modification();
"""


# Orden inverso: primero los triggers (dependen de la función), luego la
# función. Todo con IF EXISTS para ser idempotente.
REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS audit_change_log_no_modify ON audit_change_log;
DROP TRIGGER IF EXISTS audit_access_log_no_modify ON audit_access_log;
DROP FUNCTION IF EXISTS audit_prevent_modification();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
