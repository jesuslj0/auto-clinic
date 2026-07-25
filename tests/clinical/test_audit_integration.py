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
