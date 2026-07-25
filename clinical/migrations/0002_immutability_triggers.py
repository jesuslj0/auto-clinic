"""Inmutabilidad por debajo del ORM: triggers BEFORE UPDATE OR DELETE.

El modelo (`ClinicalNote.save()`, `Addendum.save()/delete()`) cierra las puertas
de Django, pero no protege frente a un `UPDATE`/`DELETE` en SQL crudo ni frente a
quien entre por `psql`. Esta migración instala la segunda barrera, en la propia
base de datos, con el mismo patrón que `audit/0002`:

- `clinical_note`: una nota ya firmada (`OLD.status = 'signed'`) no admite ni
  `UPDATE` ni `DELETE`. La transición borrador→firmada es un `UPDATE` con
  `OLD.status = 'draft'`, permitido; el borrado lógico de un borrador es un
  `UPDATE` de `deleted_at` con `OLD.status = 'draft'`, también permitido.
- `clinical_addendum`: de solo inserción, como la auditoría. Rechaza TODO
  `UPDATE` y TODO `DELETE`.

Las tablas `clinical_medical_history`, `clinical_episode` y `clinical_visit` se
apoyan en el borrado lógico del ORM y en los `on_delete=PROTECT` de sus FK; no
llevan trigger porque su contenido sigue siendo editable mientras no se firme la
nota que cuelga de ellas.
"""
from django.db import migrations


FORWARD_SQL = r"""
-- Nota clínica: inmutable una vez firmada.
CREATE OR REPLACE FUNCTION clinical_note_immutable_when_signed()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'signed' THEN
            RAISE EXCEPTION
                'clinical: DELETE no permitido sobre una nota firmada (id=%)', OLD.id
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN OLD;
    END IF;

    -- UPDATE: bloqueado si la fila YA estaba firmada. Firmar (draft->signed) y
    -- el borrado logico de un borrador pasan porque OLD.status = 'draft'.
    IF OLD.status = 'signed' THEN
        RAISE EXCEPTION
            'clinical: UPDATE no permitido sobre una nota firmada (id=%)', OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_note_no_modify_signed ON clinical_note;
CREATE TRIGGER clinical_note_no_modify_signed
    BEFORE UPDATE OR DELETE ON clinical_note
    FOR EACH ROW EXECUTE FUNCTION clinical_note_immutable_when_signed();


-- Adenda: de solo insercion.
CREATE OR REPLACE FUNCTION clinical_addendum_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'clinical: % no permitido sobre clinical_addendum (registro de solo insercion)',
        TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_addendum_no_modify ON clinical_addendum;
CREATE TRIGGER clinical_addendum_no_modify
    BEFORE UPDATE OR DELETE ON clinical_addendum
    FOR EACH ROW EXECUTE FUNCTION clinical_addendum_append_only();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS clinical_note_no_modify_signed ON clinical_note;
DROP TRIGGER IF EXISTS clinical_addendum_no_modify ON clinical_addendum;
DROP FUNCTION IF EXISTS clinical_note_immutable_when_signed();
DROP FUNCTION IF EXISTS clinical_addendum_append_only();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
