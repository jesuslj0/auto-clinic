"""El firmante (`signer_name`, `signer_relationship`) entra en lo inmutable.

Quién firma en nombre del paciente es parte de lo que prueba un consentimiento,
igual que el texto y la imagen: si se pudiera cambiar después, «firmó su madre» se
podría convertir en «firmó el paciente». La migración 0011 creó el trigger sin esos
dos campos (no existían); aquí se reemplaza la función añadiéndolos.
"""
from django.db import migrations

FORWARD_SQL = r"""
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
       OR NEW.checksum IS DISTINCT FROM OLD.checksum
       OR NEW.signer_name IS DISTINCT FROM OLD.signer_name
       OR NEW.signer_relationship IS DISTINCT FROM OLD.signer_relationship THEN
        RAISE EXCEPTION
            'clinical: el contenido de un consentimiento firmado es inmutable (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION clinical_signed_consent_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'clinical: DELETE no permitido sobre un consentimiento firmado (id=%)',
            OLD.id
            USING ERRCODE = 'raise_exception';
    END IF;
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
"""


class Migration(migrations.Migration):

    dependencies = [
        ('clinical', '0016_patient_form_link'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
