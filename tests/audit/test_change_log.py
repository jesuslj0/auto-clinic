"""Auditoría de escrituras: `ChangeLog`.

Las fixtures del conftest ya generan registros al crear clínicas y pacientes, así
que ningún test asume la tabla vacía: todos filtran por el objeto que les
importa.
"""
import json

import pytest
from django.urls import reverse

from audit import registry
from audit.context import ORIGIN_COMMAND, ORIGIN_N8N, ORIGIN_WEB
from audit.models import ChangeLog
from patients.models import Patient
from services.models import Service


def logs_for(instance):
    return ChangeLog.objects.filter(
        model_label=instance._meta.label, object_id=str(instance.pk)
    ).order_by('timestamp')


@pytest.mark.django_db
class TestChangeLogLifecycle:
    def test_create_generates_change_log(self, clinic_a):
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Ada", last_name="Lovelace", phone="+34600000001"
        )

        log = logs_for(patient).get()
        assert log.action == ChangeLog.Action.CREATE
        assert log.object_repr == "Ada Lovelace"
        assert log.patient_id == patient.pk
        assert log.changes['first_name'] == {'before': None, 'after': "Ada"}

    def test_update_generates_change_log(self, patient_a):
        patient_a.first_name = "Johnny"
        patient_a.save()

        log = logs_for(patient_a).last()
        assert log.action == ChangeLog.Action.UPDATE
        assert log.changes['first_name'] == {'before': "John", 'after': "Johnny"}

    def test_delete_generates_change_log(self, patient_a):
        pk = patient_a.pk
        patient_a.delete()

        log = ChangeLog.objects.filter(
            model_label='patients.Patient', object_id=str(pk), action=ChangeLog.Action.DELETE
        ).get()
        assert log.object_repr == "John Doe"
        # El FK queda a nulo porque la fila ya no existe; la identidad sobrevive
        # en `object_repr`.
        assert log.patient_id is None

    def test_appointment_change_is_attributed_to_its_patient(self, appointment_a, patient_a):
        appointment_a.status = 'confirmed'
        appointment_a.save()

        log = logs_for(appointment_a).last()
        assert log.action == ChangeLog.Action.UPDATE
        assert log.patient_id == patient_a.pk


@pytest.mark.django_db
class TestChangeLogDiff:
    def test_diff_only_contains_fields_that_really_changed(self, patient_a):
        patient_a.phone = "+34600123456"
        patient_a.save()

        log = logs_for(patient_a).last()
        assert set(log.changes) == {'phone'}

    def test_save_without_changes_generates_no_log(self, patient_a):
        before = logs_for(patient_a).count()
        patient_a.save()
        assert logs_for(patient_a).count() == before

    def test_noise_fields_are_excluded(self, patient_a):
        patient_a.last_name = "Doerr"
        patient_a.save()

        log = logs_for(patient_a).last()
        assert 'updated_at' not in log.changes
        assert 'created_at' not in log.changes


@pytest.mark.django_db
class TestSensitiveFields:
    def test_sensitive_field_records_the_change_but_not_the_value(self, patient_a):
        patient_a.notes = "Paciente diabético, úlcera plantar en seguimiento"
        patient_a.save()

        log = logs_for(patient_a).last()
        assert log.changes['notes'] == {'changed': True}
        assert 'diabético' not in json.dumps(log.changes, ensure_ascii=False)
        assert 'úlcera' not in json.dumps(log.changes, ensure_ascii=False)

    def test_sensitive_field_is_masked_on_create(self, clinic_a):
        patient = Patient.objects.create(
            clinic=clinic_a,
            first_name="Grace",
            last_name="Hopper",
            phone="+34600000002",
            date_of_birth="1906-12-09",
            notes="Antecedentes familiares relevantes",
        )

        changes = logs_for(patient).get().changes
        assert changes['notes'] == {'changed': True}
        assert changes['date_of_birth'] == {'changed': True}
        assert '1906' not in json.dumps(changes)

    def test_sensitive_field_is_masked_on_delete(self, patient_a):
        patient_a.notes = "Informe clínico"
        patient_a.save()
        pk = patient_a.pk
        patient_a.delete()

        log = ChangeLog.objects.filter(
            model_label='patients.Patient', object_id=str(pk), action=ChangeLog.Action.DELETE
        ).get()
        assert log.changes['notes'] == {'changed': True}

    def test_clinic_secrets_never_reach_the_log(self, clinic_a):
        clinic_a.whatsapp_token = "EAAG-super-secreto"
        clinic_a.save()

        log = logs_for(clinic_a).last()
        assert log.changes['whatsapp_token'] == {'changed': True}
        assert 'super-secreto' not in json.dumps(log.changes)


@pytest.mark.django_db
class TestUnregisteredModels:
    def test_unregistered_model_generates_no_log(self, clinic_a):
        """`Service` no está registrado: no debe generar ruido."""
        assert not registry.is_registered(Service)

        before = ChangeLog.objects.count()
        service = Service.objects.create(
            clinic=clinic_a, name="Quiropodia", duration_minutes=30, price="35.00"
        )
        service.name = "Quiropodia completa"
        service.save()
        service.delete()

        assert ChangeLog.objects.count() == before

    def test_audit_models_cannot_be_registered(self):
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(ImproperlyConfigured):
            registry.register(ChangeLog)


@pytest.mark.django_db
class TestOrigin:
    def test_outside_a_request_the_log_is_still_written(self, clinic_a):
        """Comando de gestión, shell o tarea de Celery: sin `request`."""
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Alan", last_name="Turing", phone="+34600000003"
        )

        log = logs_for(patient).get()
        assert log.user is None
        assert log.user_repr == ''
        assert log.origin == ORIGIN_COMMAND

    def test_panel_write_records_the_logged_in_user(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        response = client.post(
            reverse('patients:create'),
            {
                'first_name': 'Rosalind',
                'last_name': 'Franklin',
                'email': 'rosalind@test.com',
                'phone': '+34600000004',
                'date_of_birth': '',
                'notes': '',
            },
        )
        assert response.status_code in (200, 302)

        log = ChangeLog.objects.filter(
            model_label='patients.Patient', object_repr='Rosalind Franklin'
        ).get()
        assert log.user_id == admin_user.pk
        assert log.user_repr == 'Admin Alpha <admin@alpha.test>'
        assert log.origin == ORIGIN_WEB
        assert log.ip is not None

    def test_n8n_write_is_marked_as_such(self, api_client, clinic_a):
        api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        response = api_client.post(
            '/api/patients/',
            {
                'clinic': clinic_a.pk,
                'first_name': 'Bot',
                'last_name': 'Paciente',
                'email': 'bot@test.com',
                'phone': '+34600000005',
            },
        )
        assert response.status_code == 201

        log = ChangeLog.objects.filter(
            model_label='patients.Patient', object_id=str(response.data['id'])
        ).get()
        # El agente no es un usuario del sistema: el FK queda a nulo y la
        # identidad vive en `user_repr`.
        assert log.user is None
        assert log.user_repr == f'n8n:{clinic_a.pk}'
        assert log.origin == ORIGIN_N8N


@pytest.mark.django_db
class TestFailurePolicy:
    def test_fail_closed_aborts_the_operation(self, monkeypatch, settings, clinic_a):
        settings.AUDIT_FAILURE_POLICY = 'fail_closed'

        def boom(**kwargs):
            raise RuntimeError('disco lleno')

        monkeypatch.setattr(ChangeLog.objects, 'create', boom)

        from audit.exceptions import AuditWriteError

        with pytest.raises(AuditWriteError):
            Patient.objects.create(
                clinic=clinic_a, first_name="Nadie", last_name="Nunca", phone="+34600000006"
            )

    def test_fail_open_lets_the_operation_through(self, monkeypatch, settings, clinic_a):
        settings.AUDIT_FAILURE_POLICY = 'fail_open'

        def boom(**kwargs):
            raise RuntimeError('disco lleno')

        monkeypatch.setattr(ChangeLog.objects, 'create', boom)

        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Sí", last_name="Pasa", phone="+34600000007"
        )
        assert patient.pk is not None
