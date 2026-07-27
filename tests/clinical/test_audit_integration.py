"""Integración con `audit`: campos sensibles enmascarados y firma registrada."""
import json

import pytest

from audit.models import ChangeLog


def logs_for(instance):
    return ChangeLog.objects.filter(
        model_label=instance._meta.label, object_id=str(instance.pk)
    ).order_by('timestamp')


@pytest.mark.django_db
class TestSensitiveFieldsAreMasked:
    def test_soap_fields_never_leak_to_change_log(self, draft_note_a):
        log = logs_for(draft_note_a).filter(action=ChangeLog.Action.CREATE).get()

        for field in ('subjective', 'objective', 'assessment', 'plan'):
            assert log.changes[field] == {'changed': True}

        # Verificación cruda: ningún valor clínico aparece en el JSON del log.
        blob = json.dumps(log.changes, ensure_ascii=False)
        assert "Refiere dolor al caminar" not in blob
        assert "Hiperqueratosis" not in blob

    def test_episode_reason_is_masked(self, episode_a):
        log = logs_for(episode_a).filter(action=ChangeLog.Action.CREATE).get()
        assert log.changes['reason'] == {'changed': True}
        assert "talón" not in json.dumps(log.changes, ensure_ascii=False)


@pytest.mark.django_db
class TestPatientResolutionAndSignature:
    def test_change_log_resolves_the_patient(self, draft_note_a):
        expected = draft_note_a.visit.episode.history.patient_id
        log = logs_for(draft_note_a).filter(action=ChangeLog.Action.CREATE).get()
        assert log.patient_id == expected

    def test_signing_is_recorded_as_a_change(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)

        log = logs_for(draft_note_a).filter(action=ChangeLog.Action.UPDATE).last()
        assert log is not None
        # `status` no es sensible: se ve la transición completa.
        assert log.changes['status'] == {'before': 'draft', 'after': 'signed'}


@pytest.mark.django_db
class TestQuestionnaireAudit:
    """La anamnesis entra en el mismo trato: el cuestionario se traza entero, la
    respuesta se traza con el contenido enmascarado."""

    def test_response_snapshot_never_leaks_to_change_log(
        self, published_version_a, patient_a, episode_a
    ):
        from clinical.models import Question, QuestionnaireResponse

        question = Question.objects.filter(version=published_version_a).order_by('order').first()
        response = QuestionnaireResponse.record(
            version=published_version_a,
            patient=patient_a,
            episode=episode_a,
            answers={question.pk: True},
            source=QuestionnaireResponse.Source.PATIENT_WHATSAPP,
        )

        log = logs_for(response).filter(action=ChangeLog.Action.CREATE).get()
        assert log.changes['snapshot'] == {'changed': True}
        # El canal SÍ se ve: es justo lo que hay que poder auditar.
        assert log.changes['source']['after'] == 'patient_whatsapp'
        assert "diabético" not in json.dumps(log.changes, ensure_ascii=False)

    def test_response_is_attributed_to_the_patient(
        self, published_version_a, patient_a, episode_a
    ):
        from clinical.models import QuestionnaireResponse

        response = QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
        )
        log = logs_for(response).filter(action=ChangeLog.Action.CREATE).get()
        assert log.patient_id == patient_a.pk

    def test_publishing_a_version_is_recorded(self, draft_version_a):
        draft_version_a.publish()

        log = logs_for(draft_version_a).filter(action=ChangeLog.Action.UPDATE).first()
        assert log is not None
        assert log.changes['is_published'] == {'before': False, 'after': True}
