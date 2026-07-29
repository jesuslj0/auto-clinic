"""Motor de derivación: de la anamnesis contestada a las alertas de la ficha.

Lo que se defiende, por orden: que nunca toque una alerta manual, que sea
idempotente, y que corregir sea apagar y no borrar.
"""
import pytest

from clinical.derivation import derive_alerts
from clinical.models import (
    ClinicalAlert,
    Question,
    QuestionnaireResponse,
    TemplateVersion,
)
from clinical.rules import (
    AlertRule,
    AlertSpec,
    answer_in,
    evaluate_snapshot,
    is_affirmative,
)

# Códigos de las preguntas que el registro de reglas reconoce.
CODED_QUESTIONS = [
    ('has_diabetes', '¿Es usted diabético?'),
    ('has_peripheral_vascular_disease', '¿Le han diagnosticado problemas de circulación en las piernas?'),
    ('has_neuropathy', '¿Ha perdido sensibilidad en los pies?'),
    ('takes_anticoagulants', '¿Toma anticoagulantes (Sintrom, aspirina…)?'),
    ('allergy_latex', '¿Es alérgico al látex?'),
    ('allergy_local_anesthetics', '¿Es alérgico a los anestésicos locales?'),
]


def snapshot_entry(code, answer, text='¿Pregunta?', answer_type='boolean'):
    """Entrada de snapshot suelta, sin base de datos por medio."""
    return {
        'question_id': 1, 'code': code, 'order': 1, 'text': text,
        'answer_type': answer_type, 'required': False, 'options': [], 'answer': answer,
    }


@pytest.fixture
def coded_version(db, questionnaire_template_a):
    """Versión publicada cuyas preguntas llevan los códigos del registro."""
    version = TemplateVersion.objects.create(template=questionnaire_template_a)
    for order, (code, text) in enumerate(CODED_QUESTIONS, start=1):
        Question.objects.create(
            version=version,
            code=code,
            text=text,
            answer_type=Question.AnswerType.BOOLEAN,
            order=order,
        )
    version.publish()
    return version


def answers_for(version, **by_code):
    """`{question_id: respuesta}` a partir de los códigos, que es como se piensa."""
    ids = {q.code: q.pk for q in version.questions.all()}
    return {ids[code]: value for code, value in by_code.items()}


def record(version, patient, episode, **by_code):
    return QuestionnaireResponse.record(
        version=version, patient=patient, episode=episode,
        answers=answers_for(version, **by_code),
    )


# ---------------------------------------------------------------------------
# La parte pura: reglas sin base de datos
# ---------------------------------------------------------------------------

class TestEvaluateSnapshotIsPure:
    """Ni fixture `db` ni marca `django_db`: si tocara la base, estos fallarían."""

    def test_returns_the_spec_for_a_matching_code(self):
        specs = evaluate_snapshot([snapshot_entry('has_diabetes', True, '¿Es usted diabético?')])

        assert len(specs) == 1
        spec = specs[0]
        assert isinstance(spec, AlertSpec)
        assert spec.alert_type == ClinicalAlert.AlertType.DIABETES
        assert spec.severity == ClinicalAlert.Severity.CRITICAL
        assert spec.question_code == 'has_diabetes'
        assert '¿Es usted diabético?' in spec.note
        assert spec.note.endswith('sí')

    def test_a_negative_answer_asks_for_nothing(self):
        assert evaluate_snapshot([snapshot_entry('has_diabetes', False)]) == []

    def test_an_unanswered_question_asks_for_nothing(self):
        assert evaluate_snapshot([snapshot_entry('has_diabetes', None)]) == []

    def test_an_entry_without_code_is_ignored(self):
        """Las respuestas anteriores a que existiera `code` no rompen nada."""
        entry = snapshot_entry('has_diabetes', True)
        del entry['code']
        assert evaluate_snapshot([entry]) == []
        assert evaluate_snapshot([snapshot_entry(None, True)]) == []

    def test_matching_does_not_depend_on_order_or_text(self):
        entries = [
            snapshot_entry('allergy_latex', True, 'Redacción completamente distinta'),
            snapshot_entry('has_diabetes', True, 'Otra redacción'),
        ]
        types = {spec.alert_type for spec in evaluate_snapshot(entries)}
        assert types == {
            ClinicalAlert.AlertType.ALLERGY_LATEX,
            ClinicalAlert.AlertType.DIABETES,
        }

    def test_several_conditions_produce_several_specs(self):
        entries = [
            snapshot_entry('has_diabetes', True),
            snapshot_entry('takes_anticoagulants', True),
            snapshot_entry('allergy_latex', True),
            snapshot_entry('has_neuropathy', False),
        ]
        assert {spec.alert_type for spec in evaluate_snapshot(entries)} == {
            ClinicalAlert.AlertType.DIABETES,
            ClinicalAlert.AlertType.ANTICOAGULANTS,
            ClinicalAlert.AlertType.ALLERGY_LATEX,
        }

    def test_the_six_podiatry_rules_are_covered(self):
        entries = [snapshot_entry(code, True, text) for code, text in CODED_QUESTIONS]
        assert {spec.alert_type for spec in evaluate_snapshot(entries)} == {
            ClinicalAlert.AlertType.DIABETES,
            ClinicalAlert.AlertType.PERIPHERAL_VASCULAR_DISEASE,
            ClinicalAlert.AlertType.NEUROPATHY,
            ClinicalAlert.AlertType.ANTICOAGULANTS,
            ClinicalAlert.AlertType.ALLERGY_LATEX,
            ClinicalAlert.AlertType.ALLERGY_LOCAL_ANESTHETICS,
        }
        assert all(spec.severity == ClinicalAlert.Severity.CRITICAL for spec in evaluate_snapshot(entries))

    def test_the_same_alert_type_is_not_asked_twice(self):
        entries = [
            snapshot_entry('has_diabetes', True, 'Primera'),
            snapshot_entry('has_diabetes', True, 'Segunda'),
        ]
        specs = evaluate_snapshot(entries)
        assert len(specs) == 1
        assert 'Primera' in specs[0].note

    def test_custom_rules_can_be_injected(self):
        rule = AlertRule(
            question_code='wears_orthopedic_insoles',
            alert_type=ClinicalAlert.AlertType.OTHER,
            severity=ClinicalAlert.Severity.INFO,
            note_template='{answer}',
        )
        specs = evaluate_snapshot(
            [snapshot_entry('wears_orthopedic_insoles', True)], rules=[rule]
        )
        assert specs[0].alert_type == ClinicalAlert.AlertType.OTHER
        assert specs[0].severity == ClinicalAlert.Severity.INFO
        assert specs[0].note == 'sí'


class TestMatchers:
    def test_is_affirmative_accepts_the_usual_ways_of_saying_yes(self):
        for value in (True, 'sí', 'Si', ' YES ', 'true'):
            assert is_affirmative(value) is True

    def test_is_affirmative_rejects_everything_else(self):
        for value in (False, None, '', 'no', 0, 1, 3, [], ['sí']):
            assert is_affirmative(value) is False

    def test_answer_in_handles_single_and_multiple_choice(self):
        matcher = answer_in('Sintrom', 'Aspirina')
        assert matcher('sintrom') is True
        assert matcher(['Ibuprofeno', 'aspirina']) is True
        assert matcher('Paracetamol') is False
        assert matcher(None) is False


# ---------------------------------------------------------------------------
# El motor: reglas + base de datos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDerivation:
    def test_diabetes_creates_one_critical_derived_alert(self, coded_version, patient_a, episode_a):
        response = record(coded_version, patient_a, episode_a, has_diabetes=True)

        alert = ClinicalAlert.objects.get(patient=patient_a)
        assert alert.alert_type == ClinicalAlert.AlertType.DIABETES
        assert alert.severity == ClinicalAlert.Severity.CRITICAL
        assert alert.source == ClinicalAlert.Source.DERIVED
        assert alert.source_response == response
        assert alert.created_by is None
        assert alert.is_active is True
        assert '¿Es usted diabético?' in alert.note

    def test_derivation_runs_on_record(self, coded_version, patient_a, episode_a):
        """El alta de la respuesta ya dispara el motor, sin llamada extra."""
        record(coded_version, patient_a, episode_a, has_diabetes=True)
        assert ClinicalAlert.objects.active_critical_for(patient_a).count() == 1

    def test_derivation_can_be_skipped(self, coded_version, patient_a, episode_a):
        QuestionnaireResponse.record(
            version=coded_version, patient=patient_a, episode=episode_a,
            answers=answers_for(coded_version, has_diabetes=True),
            derive=False,
        )
        assert ClinicalAlert.objects.count() == 0

    def test_running_twice_creates_no_duplicate(self, coded_version, patient_a, episode_a):
        response = record(coded_version, patient_a, episode_a, has_diabetes=True)

        result = derive_alerts(response)   # segunda pasada sobre la misma respuesta

        assert ClinicalAlert.objects.filter(patient=patient_a).count() == 1
        assert result.created == []
        assert result.touched is False

    def test_several_conditions_create_several_alerts(self, coded_version, patient_a, episode_a):
        record(
            coded_version, patient_a, episode_a,
            has_diabetes=True, takes_anticoagulants=True, allergy_latex=True,
            has_neuropathy=False,
        )

        types = set(
            ClinicalAlert.objects.filter(patient=patient_a).values_list('alert_type', flat=True)
        )
        assert types == {'diabetes', 'anticoagulants', 'allergy_latex'}
        assert ClinicalAlert.objects.active_critical_for(patient_a).count() == 3

    def test_nothing_is_created_when_no_condition_matches(self, coded_version, patient_a, episode_a):
        record(coded_version, patient_a, episode_a, has_diabetes=False)
        assert ClinicalAlert.objects.count() == 0

    def test_a_questionnaire_without_codes_derives_nothing(self, published_version_a, patient_a, episode_a):
        """El cuestionario que no está codificado simplemente no alimenta alertas."""
        QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
        )
        assert ClinicalAlert.objects.count() == 0


@pytest.mark.django_db
class TestCorrectionsAndSuperseding:
    def test_a_newer_response_without_the_condition_deactivates_the_derived_alert(
        self, coded_version, patient_a, episode_a
    ):
        first = record(coded_version, patient_a, episode_a, has_diabetes=True)
        derived = ClinicalAlert.objects.get(source_response=first)

        record(coded_version, patient_a, episode_a, has_diabetes=False)

        derived.refresh_from_db()
        assert derived.is_active is False
        assert derived.deleted_at is None          # la fila se conserva
        assert ClinicalAlert.objects.active_critical_for(patient_a).count() == 0

    def test_a_manual_alert_is_never_touched(self, coded_version, patient_a, episode_a, professional_a):
        manual = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
            note='Lo refiere el paciente en consulta.',
            created_by=professional_a,
        )
        record(coded_version, patient_a, episode_a, has_diabetes=True)
        record(coded_version, patient_a, episode_a, has_diabetes=False)

        manual.refresh_from_db()
        assert manual.is_active is True
        assert manual.source == ClinicalAlert.Source.MANUAL
        assert manual.note == 'Lo refiere el paciente en consulta.'
        assert manual.created_by == professional_a

    def test_a_still_supported_condition_stays_visible_only_once(
        self, coded_version, patient_a, episode_a
    ):
        first = record(coded_version, patient_a, episode_a, has_diabetes=True)
        second = record(coded_version, patient_a, episode_a, has_diabetes=True)

        active = ClinicalAlert.objects.active_critical_for(patient_a)
        assert active.count() == 1
        assert active.first().source_response == second
        # La anterior no se borra: queda apagada como historia.
        assert ClinicalAlert.objects.get(source_response=first).is_active is False

    def test_alerts_from_another_questionnaire_are_left_alone(
        self, coded_version, questionnaire_template_a, clinic_a, patient_a, episode_a
    ):
        from clinical.models import QuestionnaireTemplate

        other_template = QuestionnaireTemplate.objects.create(
            clinic=clinic_a, name='Anamnesis de otra cosa',
        )
        other_version = TemplateVersion.objects.create(template=other_template)
        Question.objects.create(
            version=other_version, code='allergy_latex', text='¿Alergia al látex?',
            answer_type=Question.AnswerType.BOOLEAN, order=1,
        )
        other_version.publish()

        latex = record(other_version, patient_a, episode_a, allergy_latex=True)
        record(coded_version, patient_a, episode_a, has_diabetes=True)

        alert = ClinicalAlert.objects.get(source_response=latex)
        assert alert.is_active is True

    def test_a_reappearing_condition_reuses_the_same_row(self, coded_version, patient_a, episode_a):
        response = record(coded_version, patient_a, episode_a, has_diabetes=True)
        alert = ClinicalAlert.objects.get(source_response=response)
        alert.deactivate()

        result = derive_alerts(response)

        alert.refresh_from_db()
        assert alert.is_active is True
        assert result.reactivated == [alert]
        assert ClinicalAlert.objects.filter(patient=patient_a).count() == 1

    def test_an_older_response_derived_late_does_not_supersede_the_newer(
        self, coded_version, patient_a, episode_a
    ):
        """Manda `filled_at`, no el orden en que se derive."""
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        old = QuestionnaireResponse.record(
            version=coded_version, patient=patient_a, episode=episode_a,
            answers=answers_for(coded_version, has_diabetes=True),
            filled_at=now - timedelta(days=30), derive=False,
        )
        new = record(coded_version, patient_a, episode_a, has_diabetes=True)

        derive_alerts(old)   # se deriva la vieja DESPUÉS

        assert ClinicalAlert.objects.get(source_response=new).is_active is True


@pytest.mark.django_db
class TestDerivedAlertsAreAudited:
    def test_the_alert_and_its_deactivation_are_recorded(self, coded_version, patient_a, episode_a):
        from audit.models import ChangeLog

        record(coded_version, patient_a, episode_a, has_diabetes=True)
        alert = ClinicalAlert.objects.get(patient=patient_a)

        created = ChangeLog.objects.filter(
            model_label='clinical.ClinicalAlert', object_id=str(alert.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert created.changes['source']['after'] == 'derived'
        assert created.changes['note'] == {'changed': True}   # el texto no se filtra
        assert created.patient_id == patient_a.pk

        record(coded_version, patient_a, episode_a, has_diabetes=False)

        updated = ChangeLog.objects.filter(
            model_label='clinical.ClinicalAlert', object_id=str(alert.pk),
            action=ChangeLog.Action.UPDATE,
        ).latest('timestamp')
        assert updated.changes['is_active'] == {'before': True, 'after': False}


@pytest.mark.django_db
class TestQuestionCode:
    def test_the_code_travels_frozen_inside_the_snapshot(self, coded_version, patient_a, episode_a):
        response = record(coded_version, patient_a, episode_a, has_diabetes=True)

        codes = [entry['code'] for entry in response.snapshot]
        assert 'has_diabetes' in codes

    def test_the_code_survives_the_question_disappearing(self, coded_version, patient_a, episode_a):
        """El snapshot se interpreta solo: no consulta la pregunta viva."""
        response = record(coded_version, patient_a, episode_a, has_diabetes=True)

        coded_version.unpublish()
        Question.objects.get(version=coded_version, code='has_diabetes').delete()

        response.refresh_from_db()
        assert evaluate_snapshot(response.snapshot)[0].alert_type == 'diabetes'

    def test_two_questions_cannot_share_a_code_in_one_version(self, questionnaire_template_a):
        from django.db import IntegrityError, transaction

        version = TemplateVersion.objects.create(template=questionnaire_template_a)
        Question.objects.create(
            version=version, code='has_diabetes', text='¿Diabético?',
            answer_type=Question.AnswerType.BOOLEAN, order=1,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Question.objects.create(
                    version=version, code='has_diabetes', text='¿Diabetes?',
                    answer_type=Question.AnswerType.BOOLEAN, order=2,
                )

    def test_uncoded_questions_do_not_collide(self, draft_version_a):
        """Varias preguntas sin código conviven: el índice único no las mira."""
        assert Question.objects.filter(version=draft_version_a, code__isnull=True).count() == 3

    def test_the_same_code_repeats_across_versions(self, coded_version, questionnaire_template_a):
        v2 = questionnaire_template_a.new_draft_version()
        assert v2.questions.filter(code='has_diabetes').exists()
