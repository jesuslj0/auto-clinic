"""Alertas clínicas: alta manual, baja que conserva la fila y consulta de la ficha.

Lo que se defiende aquí: una alerta **nunca desaparece**. Se apaga, y la fila
queda para poder responder después a «esto se sabía en aquel momento».
"""
import pytest
from django.core.exceptions import ValidationError

from clinical.models import ClinicalAlert
from core.managers import ProtectedRecordError


@pytest.fixture
def alert_a(db, patient_a, professional_a):
    return ClinicalAlert.objects.create(
        patient=patient_a,
        alert_type=ClinicalAlert.AlertType.DIABETES,
        severity=ClinicalAlert.Severity.CRITICAL,
        note='Diabetes tipo II en tratamiento con metformina.',
        created_by=professional_a,
    )


@pytest.mark.django_db
class TestManualAlert:
    def test_defaults_to_manual_without_source_response(self, alert_a):
        assert alert_a.source == ClinicalAlert.Source.MANUAL
        assert alert_a.source_response is None
        assert alert_a.is_active is True
        assert alert_a.created_at is not None

    def test_records_its_author(self, alert_a, professional_a):
        assert alert_a.created_by == professional_a

    def test_author_is_optional(self, db, patient_a):
        alert = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.ALLERGY_LATEX,
            severity=ClinicalAlert.Severity.CRITICAL,
        )
        assert alert.created_by is None

    def test_the_podiatry_critical_types_are_available(self):
        values = set(ClinicalAlert.AlertType.values)
        assert {
            'diabetes', 'peripheral_vascular_disease', 'neuropathy',
            'anticoagulants', 'allergy_latex', 'allergy_local_anesthetics', 'other',
        } <= values


@pytest.mark.django_db
class TestDeactivation:
    def test_deactivating_preserves_the_row(self, alert_a):
        alert_a.deactivate()

        alert_a.refresh_from_db()
        assert alert_a.is_active is False
        assert alert_a.deleted_at is None
        assert ClinicalAlert.objects.filter(pk=alert_a.pk).exists()
        # El detalle sigue ahí: desactivar no borra lo que decía la alerta.
        assert alert_a.note.startswith('Diabetes tipo II')

    def test_deactivating_twice_is_harmless(self, alert_a):
        alert_a.deactivate()
        alert_a.deactivate()

        alert_a.refresh_from_db()
        assert alert_a.is_active is False

    def test_can_be_reactivated(self, alert_a):
        alert_a.deactivate()
        alert_a.reactivate()

        alert_a.refresh_from_db()
        assert alert_a.is_active is True

    def test_an_alert_cannot_be_deleted(self, alert_a):
        assert alert_a.can_be_deleted() is False
        with pytest.raises(ProtectedRecordError):
            alert_a.delete()
        assert ClinicalAlert.all_objects.filter(pk=alert_a.pk).exists()

    def test_queryset_delete_is_blocked_too(self, alert_a):
        with pytest.raises(ProtectedRecordError):
            ClinicalAlert.objects.filter(pk=alert_a.pk).delete()
        assert ClinicalAlert.all_objects.filter(pk=alert_a.pk).exists()


@pytest.mark.django_db
class TestActiveCriticalFor:
    def test_returns_only_active_critical_of_that_patient(self, patient_a, patient_b, professional_a):
        critical = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.ANTICOAGULANTS,
            severity=ClinicalAlert.Severity.CRITICAL,
        )
        # Ruido que NO debe salir: otra gravedad, desactivada, y de otro paciente.
        ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.OTHER,
            severity=ClinicalAlert.Severity.WARNING,
            note='Ansiedad ante el sillón.',
        )
        deactivated = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.NEUROPATHY,
            severity=ClinicalAlert.Severity.CRITICAL,
        )
        deactivated.deactivate()
        ClinicalAlert.objects.create(
            patient=patient_b,
            alert_type=ClinicalAlert.AlertType.ALLERGY_LATEX,
            severity=ClinicalAlert.Severity.CRITICAL,
        )

        result = ClinicalAlert.objects.active_critical_for(patient_a)

        assert list(result) == [critical]

    def test_orders_most_recent_first(self, patient_a):
        first = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
        )
        second = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.ALLERGY_LOCAL_ANESTHETICS,
            severity=ClinicalAlert.Severity.CRITICAL,
        )

        assert list(ClinicalAlert.objects.active_critical_for(patient_a)) == [second, first]

    def test_is_empty_for_a_patient_without_alerts(self, patient_a):
        assert list(ClinicalAlert.objects.active_critical_for(patient_a)) == []

    def test_the_queryset_helpers_chain(self, alert_a, patient_a):
        assert ClinicalAlert.objects.for_patient(patient_a).active().critical().count() == 1


@pytest.mark.django_db
class TestSourceCoherence:
    """El esquema ya sostiene el motor de derivación, pero sin mentir sobre él."""

    def test_a_derived_alert_needs_its_source_response(self, patient_a):
        alert = ClinicalAlert(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
            source=ClinicalAlert.Source.DERIVED,
        )
        with pytest.raises(ValidationError):
            alert.save()

    def test_a_manual_alert_cannot_point_to_a_response(self, patient_a, published_version_a, episode_a):
        from clinical.models import QuestionnaireResponse

        response = QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
        )
        alert = ClinicalAlert(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
            source=ClinicalAlert.Source.MANUAL,
            source_response=response,
        )
        with pytest.raises(ValidationError):
            alert.save()

    def test_a_derived_alert_keeps_its_provenance(self, patient_a, published_version_a, episode_a):
        from clinical.models import QuestionnaireResponse

        response = QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
        )
        alert = ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
            source=ClinicalAlert.Source.DERIVED,
            source_response=response,
        )

        assert alert.created_by is None
        assert list(response.derived_alerts.all()) == [alert]

    def test_a_response_with_derived_alerts_is_protected(self, patient_a, published_version_a, episode_a):
        """La procedencia no puede evaporarse: la FK es PROTECT."""
        from django.db import transaction
        from django.db.models import ProtectedError

        from clinical.models import QuestionnaireResponse

        response = QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
        )
        ClinicalAlert.objects.create(
            patient=patient_a,
            alert_type=ClinicalAlert.AlertType.DIABETES,
            severity=ClinicalAlert.Severity.CRITICAL,
            source=ClinicalAlert.Source.DERIVED,
            source_response=response,
        )

        with pytest.raises(ProtectedError):
            with transaction.atomic():
                QuestionnaireResponse.all_objects.filter(pk=response.pk).hard_delete()


@pytest.mark.django_db
class TestAudit:
    def test_the_note_never_leaks_and_the_patient_is_resolved(self, alert_a):
        import json

        from audit.models import ChangeLog

        log = ChangeLog.objects.filter(
            model_label='clinical.ClinicalAlert', object_id=str(alert_a.pk),
            action=ChangeLog.Action.CREATE,
        ).get()

        assert log.changes['note'] == {'changed': True}
        assert log.changes['alert_type']['after'] == 'diabetes'
        assert log.patient_id == alert_a.patient_id
        assert 'metformina' not in json.dumps(log.changes, ensure_ascii=False)

    def test_deactivating_is_recorded(self, alert_a):
        from audit.models import ChangeLog

        alert_a.deactivate()

        log = ChangeLog.objects.filter(
            model_label='clinical.ClinicalAlert', object_id=str(alert_a.pk),
            action=ChangeLog.Action.UPDATE,
        ).latest('timestamp')
        assert log.changes['is_active'] == {'before': True, 'after': False}


@pytest.mark.django_db
class TestAdmin:
    def test_alert_pages_are_reachable(self, admin_site_client, alert_a):
        for url in (
            '/admin/clinical/clinicalalert/',
            f'/admin/clinical/clinicalalert/{alert_a.pk}/change/',
            '/admin/clinical/clinicalalert/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_alerts_cannot_be_deleted_from_the_admin(self, alert_a):
        from django.contrib.admin.sites import site

        assert site._registry[ClinicalAlert].has_delete_permission(request=None, obj=alert_a) is False

    def test_deactivate_action_keeps_the_row(self, admin_site_client, alert_a):
        response = admin_site_client.post(
            '/admin/clinical/clinicalalert/',
            {'action': 'deactivate_alerts', '_selected_action': [str(alert_a.pk)]},
            follow=True,
        )
        assert response.status_code == 200

        alert_a.refresh_from_db()
        assert alert_a.is_active is False
        assert ClinicalAlert.objects.filter(pk=alert_a.pk).exists()

    def test_creating_from_the_admin_signs_the_current_professional(self, admin_site_client, patient_a, superuser):
        from appointments.models import Professional

        professional = Professional.objects.create(clinic=patient_a.clinic, user=superuser)

        admin_site_client.post(
            '/admin/clinical/clinicalalert/add/',
            {
                'patient': patient_a.pk,
                'alert_type': ClinicalAlert.AlertType.NEUROPATHY,
                'severity': ClinicalAlert.Severity.CRITICAL,
                'note': 'Pérdida de sensibilidad distal.',
                'is_active': 'on',
            },
        )

        alert = ClinicalAlert.objects.get(patient=patient_a, alert_type='neuropathy')
        assert alert.created_by == professional
        assert alert.source == ClinicalAlert.Source.MANUAL
