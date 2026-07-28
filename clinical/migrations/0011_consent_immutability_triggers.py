"""Inmutabilidad del consentimiento por debajo del ORM.

Misma doctrina que las migraciones 0002 y 0004: lo que el modelo cierra en
Django, el trigger lo cierra también frente a un `UPDATE`/`DELETE` en SQL crudo o
desde `psql`. Dos piezas:

- `clinical_signed_consent`: una firma es inmutable desde que existe (versión,
  paciente, episodio, fecha, la copia del texto y el fichero de la firma con su
  huella) y la fila no se borra físicamente jamás. Lo único que puede cambiar es
  `deleted_at`/`updated_at`, que es el borrado lógico.
- `clinical_consent_version`: una versión publicada no cambia de documento, de
  número, de fecha de publicación **ni de texto**, y no se borra físicamente. El
  texto entra en la lista —a diferencia de la versión de cuestionario, cuyo
  contenido son sus `Question`s— porque aquí el documento *es* el texto: dejarlo
  editable sería dejar abierta la puerta a reescribir lo que alguien firmó.
  `is_current` e `is_published` sí se pueden mover: son ciclo de vida (cambiar la
  vigente, retirar el documento) y no alteran ninguna firma, porque cada una
  lleva su propia copia.
"""
from django.db import migrations


FORWARD_SQL = r"""
-- Consentimiento firmado: congelado al recogerse.
CREATE OR REPLACE FUNCTION clinical_signed_consent_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'clinical: DELETE no permitido sobre un consentimiento firmado (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;

    -- UPDATE: solo pasa si no toca el contenido. El borrado logico (deleted_at)
    -- y updated_at quedan fuera de la comparacion a proposito.
    IF NEW.text_copy IS DISTINCT FROM OLD.text_copy
       OR NEW.signature_image IS DISTINCT FROM OLD.signature_image
       OR NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
       OR NEW.episode_id IS DISTINCT FROM OLD.episode_id
       OR NEW.signed_at IS DISTINCT FROM OLD.signed_at
       OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
       OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
       OR NEW.checksum IS DISTINCT FROM OLD.checksum THEN
        RAISE EXCEPTION
            'clinical: el contenido de un consentimiento firmado es inmutable (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_signed_consent_no_modify ON clinical_signed_consent;
CREATE TRIGGER clinical_signed_consent_no_modify
    BEFORE UPDATE OR DELETE ON clinical_signed_consent
    FOR EACH ROW EXECUTE FUNCTION clinical_signed_consent_immutable();


-- Version de consentimiento: texto congelado tras publicarse.
CREATE OR REPLACE FUNCTION clinical_consent_version_frozen_when_published()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_published THEN
            RAISE EXCEPTION
                'clinical: DELETE no permitido sobre una version de consentimiento publicada (id=%)',
                OLD.id
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.is_published AND (
           NEW.template_id IS DISTINCT FROM OLD.template_id
        OR NEW.number IS DISTINCT FROM OLD.number
        OR NEW.text IS DISTINCT FROM OLD.text
        OR NEW.published_at IS DISTINCT FROM OLD.published_at
    ) THEN
        RAISE EXCEPTION
            'clinical: el texto de una version de consentimiento publicada es inmutable (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clinical_consent_version_no_modify_published ON clinical_consent_version;
CREATE TRIGGER clinical_consent_version_no_modify_published
    BEFORE UPDATE OR DELETE ON clinical_consent_version
    FOR EACH ROW EXECUTE FUNCTION clinical_consent_version_frozen_when_published();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS clinical_signed_consent_no_modify ON clinical_signed_consent;
DROP TRIGGER IF EXISTS clinical_consent_version_no_modify_published ON clinical_consent_version;
DROP FUNCTION IF EXISTS clinical_signed_consent_immutable();
DROP FUNCTION IF EXISTS clinical_consent_version_frozen_when_published();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0010_consents'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
