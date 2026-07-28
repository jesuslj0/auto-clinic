"""Comando `normalize_phones`: normaliza y **deja rastro**.

Lo que se vigila aquí no es el formato E.164 (eso ya se prueba en
`test_phone_normalization.py`), sino que el cambio pase por el ORM: `Patient` y
`Clinic` están auditados, y con un `queryset.update()` el teléfono cambiaría sin
`ChangeLog`.

El log no se puede limpiar entre pasos —es de solo inserción, por diseño—, así
que las aserciones filtran por acción en lugar de contar sobre una tabla vacía.
"""
import pytest
from django.core.management import call_command

from audit.models import ChangeLog
from patients.models import Patient


def updates_for(instance):
    """Modificaciones registradas de una instancia, de la más antigua a la última."""
    return ChangeLog.objects.filter(
        model_label=instance._meta.label,
        object_id=str(instance.pk),
        action=ChangeLog.Action.UPDATE,
    ).order_by('timestamp')


@pytest.fixture
def patient_with_raw_phone(db, clinic_a):
    return Patient.objects.create(
        clinic=clinic_a,
        first_name="Ana",
        last_name="Ruiz",
        email="ana.ruiz@example.com",
        phone="+34 687 95 74 99",
    )


@pytest.mark.django_db
class TestNormalizePhonesCommand:
    def test_normalizes_the_stored_phone(self, patient_with_raw_phone):
        call_command('normalize_phones')

        patient_with_raw_phone.refresh_from_db()
        assert patient_with_raw_phone.phone == "+34687957499"

    def test_the_change_is_audited(self, patient_with_raw_phone):
        call_command('normalize_phones')

        log = updates_for(patient_with_raw_phone).get()
        assert log.changes['phone'] == {
            'before': "+34 687 95 74 99",
            'after': "+34687957499",
        }
        assert log.origin == 'command'
        assert log.user_repr == 'comando normalize_phones'

    def test_touching_nothing_leaves_no_log(self, db, clinic_a):
        """Un teléfono ya normalizado no es un evento: no debe ensuciar el log."""
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Ya", last_name="Correcto",
            email="ok@example.com", phone="+34687957499",
        )

        call_command('normalize_phones')

        assert updates_for(patient).count() == 0

    def test_invalid_phone_is_skipped_and_kept(self, db, clinic_a):
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Sin", last_name="Teléfono",
            email="raro@example.com", phone="no es un teléfono",
        )

        call_command('normalize_phones')

        patient.refresh_from_db()
        assert patient.phone == "no es un teléfono"
        assert updates_for(patient).count() == 0

    def test_the_clinic_phone_number_id_is_left_alone(self, db, clinic_a):
        """No es un teléfono: es el Phone Number ID de Meta.

        Va tal cual en la URL de la Graph API y es la clave con la que el webhook
        localiza la clínica. Pasarlo a E.164 rompía las dos cosas.
        """
        clinic_a.whatsapp_phone_number_id = "677584902"
        clinic_a.save(update_fields=['whatsapp_phone_number_id'])
        before = updates_for(clinic_a).count()

        call_command('normalize_phones')

        clinic_a.refresh_from_db()
        assert clinic_a.whatsapp_phone_number_id == "677584902"
        assert updates_for(clinic_a).count() == before

    def test_a_unicity_clash_does_not_abort_the_rest(self, db, clinic_a):
        """El choque se informa y se sigue: lo demás sí queda normalizado."""
        Patient.objects.create(
            clinic=clinic_a, first_name="Primero", last_name="Uno",
            email="dup@example.com", phone="+34687957499",
        )
        clash = Patient.objects.create(
            clinic=clinic_a, first_name="Segundo", last_name="Dos",
            email="dup@example.com", phone="+34 687 95 74 99",
        )
        other = Patient.objects.create(
            clinic=clinic_a, first_name="Tercero", last_name="Tres",
            email="otro@example.com", phone="687 95 74 88",
        )

        call_command('normalize_phones')

        clash.refresh_from_db()
        other.refresh_from_db()
        assert clash.phone == "+34 687 95 74 99"   # intacto
        assert other.phone == "+34687957488"       # normalizado igualmente
        assert updates_for(clash).count() == 0
