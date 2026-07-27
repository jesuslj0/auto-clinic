"""Comando `seed_clinical`: datos de ejemplo para desarrollo.

No se prueba el contenido de los casos clínicos (es texto de relleno), sino lo
que sí importa: que siembre una historia coherente, que sea repetible sin
duplicar y que el `--dry-run` no escriba.
"""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from clinical.models import (
    ClinicalNote,
    Episode,
    MedicalHistory,
    QuestionnaireResponse,
    QuestionnaireTemplate,
)


@pytest.mark.django_db
class TestSeedClinical:

    @pytest.fixture(autouse=True)
    def development_mode(self, settings):
        """El runner de Django fuerza `DEBUG=False`, y el comando lo exige `True`.

        Se repone aquí porque es justo la condición de desarrollo bajo la que el
        comando está pensado para correr.
        """
        settings.DEBUG = True
    def test_seeds_a_coherent_history(self, clinic_a, patient_a, professional_a):
        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        history = MedicalHistory.objects.get(patient=patient_a)
        assert history.episodes.count() == 2
        assert history.episodes.filter(status=Episode.Status.CLOSED).count() == 1

        notes = ClinicalNote.objects.filter(visit__episode__history=history)
        signed = notes.get(status=ClinicalNote.Status.SIGNED)
        assert signed.content_hash.startswith('sha256:')
        assert signed.addenda.count() == 1
        assert notes.filter(status=ClinicalNote.Status.DRAFT).count() == 1

    def test_seeds_the_questionnaire_with_two_published_versions(self, clinic_a, patient_a, professional_a):
        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        template = QuestionnaireTemplate.objects.get(clinic=clinic_a)
        published = template.versions.filter(is_published=True)
        assert published.count() == 2
        assert template.current_version.number == 2

        response = QuestionnaireResponse.objects.get(patient=patient_a)
        assert len(response.snapshot) == len(response.version.questions.all())
        assert response.snapshot[0]['text'].startswith('¿Padece alguna enfermedad')

    def test_running_twice_does_not_duplicate(self, clinic_a, patient_a, professional_a):
        call_command('seed_clinical', clinic=clinic_a.clinic_id)
        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        assert Episode.objects.filter(history__patient=patient_a).count() == 2
        assert QuestionnaireTemplate.objects.filter(clinic=clinic_a).count() == 1
        assert QuestionnaireResponse.objects.filter(patient=patient_a).count() == 1

    def test_dry_run_writes_nothing(self, clinic_a, patient_a, professional_a):
        call_command('seed_clinical', clinic=clinic_a.clinic_id, dry_run=True)

        assert Episode.objects.count() == 0
        assert QuestionnaireTemplate.objects.count() == 0

    def test_a_clinic_without_professionals_is_skipped(self, clinic_b, patient_b):
        call_command('seed_clinical', clinic=clinic_b.clinic_id)

        # Una visita necesita profesional: sin él no se siembra nada, pero
        # tampoco revienta.
        assert Episode.objects.count() == 0

    def test_refuses_to_run_outside_development(self, settings, clinic_a, patient_a, professional_a):
        settings.DEBUG = False
        with pytest.raises(CommandError):
            call_command('seed_clinical', clinic=clinic_a.clinic_id)
        assert Episode.objects.count() == 0
