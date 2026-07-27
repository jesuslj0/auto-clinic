"""Inmutabilidad de la anamnesis por debajo del ORM.

Misma doctrina que la migración 0002: lo que el modelo cierra en Django, el
trigger lo cierra también frente a un `UPDATE`/`DELETE` en SQL crudo o desde
`psql`. Tres piezas:

- `clinical_questionnaire_response`: el contenido de una respuesta es inmutable
  desde que existe (snapshot, versión, paciente, episodio, canal, fecha y autor)
  y la fila no se borra físicamente jamás. Lo único que puede cambiar es
  `deleted_at`/`updated_at`, que es el borrado lógico.
- `clinical_question`: las preguntas de una versión publicada no admiten
  `INSERT`, `UPDATE` ni `DELETE`. El `INSERT` entra a propósito: añadir una
  pregunta a una versión ya publicada cambiaría el documento tanto como editar
  una existente.
- `clinical_template_version`: una versión publicada no cambia de cuestionario,
  de número ni de fecha de publicación, y no se borra físicamente. `is_current` e
  `is_published` sí se pueden mover: son ciclo de vida (cambiar la vigente,
  retirar el cuestionario) y no alteran ninguna respuesta ya guardada, porque
  cada una lleva su propio snapshot.
"""
from django.db import migrations


FORWARD_SQL = r"""
-- Respuesta de cuestionario: congelada al guardarse.
CREATE OR REPLACE FUNCTION clinical_response_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'clinical: DELETE no permitido sobre una respuesta de cuestionario (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;

    -- UPDATE: solo pasa si no toca el contenido. El borrado logico (deleted_at)
    -- y updated_at quedan fuera de la comparacion a proposito.
    IF NEW.snapshot IS DISTINCT FROM OLD.snapshot
       OR NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
       OR NEW.episode_id IS DISTINCT FROM OLD.episode_id
       OR NEW.filled_at IS DISTINCT FROM OLD.filled_at
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id THEN
        RAISE EXCEPTION
            'clinical: el contenido de una respuesta de cuestionario es inmutable (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_response_no_modify ON clinical_questionnaire_response;
CREATE TRIGGER clinical_response_no_modify
    BEFORE UPDATE OR DELETE ON clinical_questionnaire_response
    FOR EACH ROW EXECUTE FUNCTION clinical_response_immutable();


-- Preguntas: congeladas cuando su version esta publicada.
CREATE OR REPLACE FUNCTION clinical_question_frozen_when_published()
RETURNS TRIGGER AS $$
DECLARE
    v_version_id BIGINT;
    v_published BOOLEAN;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_version_id := OLD.version_id;
    ELSE
        v_version_id := NEW.version_id;
    END IF;

    SELECT is_published INTO v_published
      FROM clinical_template_version
     WHERE id = v_version_id;

    IF COALESCE(v_published, FALSE) THEN
        RAISE EXCEPTION
            'clinical: % no permitido sobre las preguntas de la version publicada %',
            TG_OP, v_version_id
            USING ERRCODE = 'raise_exception';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_question_no_modify_published ON clinical_question;
CREATE TRIGGER clinical_question_no_modify_published
    BEFORE INSERT OR UPDATE OR DELETE ON clinical_question
    FOR EACH ROW EXECUTE FUNCTION clinical_question_frozen_when_published();


-- Version de cuestionario: contenido congelado tras publicarse.
CREATE OR REPLACE FUNCTION clinical_template_version_frozen_when_published()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_published THEN
            RAISE EXCEPTION
                'clinical: DELETE no permitido sobre una version publicada (id=%)',
                OLD.id
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.is_published AND (
           NEW.template_id IS DISTINCT FROM OLD.template_id
        OR NEW.number IS DISTINCT FROM OLD.number
        OR NEW.published_at IS DISTINCT FROM OLD.published_at
    ) THEN
        RAISE EXCEPTION
            'clinical: el contenido de una version publicada es inmutable (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_template_version_no_modify_published ON clinical_template_version;
CREATE TRIGGER clinical_template_version_no_modify_published
    BEFORE UPDATE OR DELETE ON clinical_template_version
    FOR EACH ROW EXECUTE FUNCTION clinical_template_version_frozen_when_published();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS clinical_response_no_modify ON clinical_questionnaire_response;
DROP TRIGGER IF EXISTS clinical_question_no_modify_published ON clinical_question;
DROP TRIGGER IF EXISTS clinical_template_version_no_modify_published ON clinical_template_version;
DROP FUNCTION IF EXISTS clinical_response_immutable();
DROP FUNCTION IF EXISTS clinical_question_frozen_when_published();
DROP FUNCTION IF EXISTS clinical_template_version_frozen_when_published();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0003_questionnaires'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
