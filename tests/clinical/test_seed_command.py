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
        # Las preguntas que alimentan el motor de alertas van codificadas.
        codes = {entry['code'] for entry in response.snapshot}
        assert 'has_diabetes' in codes

    def test_running_twice_does_not_duplicate(self, clinic_a, patient_a, professional_a):
        call_command('seed_clinical', clinic=clinic_a.clinic_id)
        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        assert Episode.objects.filter(history__patient=patient_a).count() == 2
        assert QuestionnaireTemplate.objects.filter(clinic=clinic_a).count() == 1
        assert QuestionnaireResponse.objects.filter(patient=patient_a).count() == 1

    def test_a_declared_condition_seeds_its_derived_alert(self, clinic_a, patient_a, patient_b, professional_a):
        """El seed recorre la cadena entera: anamnesis → motor → alerta."""
        from patients.models import Patient

        from clinical.models import ClinicalAlert

        # `patient_b` es de otra clínica; se crea uno más en clinic_a para que el
        # segundo caso (el que declara condiciones) también se siembre.
        Patient.objects.create(
            clinic=clinic_a, first_name="Con", last_name="Condiciones",
            email="condiciones@example.com", phone="+34600111222",
        )

        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        derived = ClinicalAlert.objects.filter(source=ClinicalAlert.Source.DERIVED)
        assert set(derived.values_list('alert_type', flat=True)) == {
            # Del primer caso sembrado: alergia a anestésicos locales.
            'allergy_local_anesthetics',
            # Del segundo: diabetes, anticoagulantes y alergia al látex.
            'diabetes', 'anticoagulants', 'allergy_latex',
        }
        assert all(alert.source_response_id is not None for alert in derived)
        assert all(alert.created_by is None for alert in derived)

    def test_refresh_anamnesis_adds_a_response_without_touching_the_history(
        self, clinic_a, patient_a, professional_a
    ):
        call_command('seed_clinical', clinic=clinic_a.clinic_id)
        episodes = Episode.objects.filter(history__patient=patient_a).count()
        notes = ClinicalNote.objects.filter(visit__episode__history__patient=patient_a).count()

        call_command('seed_clinical', clinic=clinic_a.clinic_id, refresh_anamnesis=True)

        # La anamnesis nueva no arrastra episodios, visitas ni notas.
        assert Episode.objects.filter(history__patient=patient_a).count() == episodes
        assert ClinicalNote.objects.filter(visit__episode__history__patient=patient_a).count() == notes
        assert QuestionnaireResponse.objects.filter(patient=patient_a).count() == 2

    def test_refresh_publishes_a_coded_version_when_the_current_one_has_none(
        self, clinic_a, patient_a, professional_a
    ):
        """Una versión publicada es inmutable: los códigos entran en una nueva."""
        from clinical.models import Question, QuestionnaireTemplate, TemplateVersion

        template = QuestionnaireTemplate.objects.create(
            clinic=clinic_a, name='Anamnesis dental',
        )
        legacy = TemplateVersion.objects.create(template=template)
        Question.objects.create(
            version=legacy, text='¿Alguna enfermedad?',
            answer_type=Question.AnswerType.BOOLEAN, order=1,
        )
        legacy.publish()
        assert legacy.questions.exclude(code=None).count() == 0

        call_command('seed_clinical', clinic=clinic_a.clinic_id)

        legacy.refresh_from_db()
        current = template.current_version
        assert current.number > legacy.number
        assert current.questions.exclude(code=None).exists()
        # La antigua se conserva publicada e intacta, solo deja de ser la vigente.
        assert legacy.is_published is True
        assert legacy.is_current is False
        assert legacy.questions.count() == 1

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
