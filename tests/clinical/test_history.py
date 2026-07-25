"""Historia clínica: creación automática, unicidad y numeración."""
import pytest
from django.utils import timezone

from clinical.models import MedicalHistory
from patients.models import Patient


@pytest.mark.django_db
class TestHistoryAutoCreation:
    def test_creating_patient_creates_history(self, clinic_a):
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Ada", last_name="Byron", phone="+34600000021"
        )
        history = MedicalHistory.all_objects.get(patient=patient)
        assert history.number.startswith(f'HC-{timezone.now().year}-')
        assert history.clinic_id == clinic_a.pk

    def test_no_patient_without_history(self, patient_a):
        assert MedicalHistory.all_objects.filter(patient=patient_a).exists()

    def test_history_never_creates_duplicate_on_resave(self, patient_a):
        patient_a.first_name = "Cambiado"
        patient_a.save()
        assert MedicalHistory.all_objects.filter(patient=patient_a).count() == 1


@pytest.mark.django_db
class TestHistoryNumbering:
    def test_number_is_correlative_per_clinic_and_year(self, clinic_a, clinic_b):
        year = timezone.now().year
        p1 = Patient.objects.create(
            clinic=clinic_a, first_name="1", last_name="X", phone="+34600000031"
        )
        p2 = Patient.objects.create(
            clinic=clinic_a, first_name="2", last_name="X", phone="+34600000032"
        )
        assert p1.medical_history.number == f'HC-{year}-00001'
        assert p2.medical_history.number == f'HC-{year}-00002'

        # Otra clínica reinicia su propio correlativo.
        p3 = Patient.objects.create(
            clinic=clinic_b, first_name="3", last_name="X", phone="+34600000033"
        )
        assert p3.medical_history.number == f'HC-{year}-00001'
