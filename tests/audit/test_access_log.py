"""Auditoría de lecturas: `AccessLog`."""
import pytest
from django.urls import reverse

from audit.context import ORIGIN_API, ORIGIN_WEB
from audit.mixins import log_access
from audit.models import AccessLog


@pytest.mark.django_db
class TestPanelViews:
    def test_detail_view_logs_the_right_patient(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        response = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))
        assert response.status_code == 200

        log = AccessLog.objects.filter(action=AccessLog.Action.VIEW).get()
        assert log.patient_id == patient_a.pk
        assert log.object_repr == "John Doe"
        assert log.user_id == admin_user.pk
        assert log.origin == ORIGIN_WEB
        assert log.method == 'GET'

    def test_list_view_logs_the_result_count(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        response = client.get(reverse('patients:list'))
        assert response.status_code == 200

        log = AccessLog.objects.filter(action=AccessLog.Action.LIST).get()
        assert log.patient_id is None
        assert log.result_count == 1

    def test_search_is_distinguished_from_a_plain_listing(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        client.get(reverse('patients:list'), {'q': 'Doe'})

        log = AccessLog.objects.get()
        assert log.action == AccessLog.Action.SEARCH
        assert log.result_count == 1

    def test_anonymous_visitor_generates_no_access_log(self, client, patient_a):
        """LoginRequiredMixin corta en `dispatch`: no hay lectura que registrar."""
        response = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))
        assert response.status_code == 302
        assert not AccessLog.objects.exists()

    def test_a_404_is_not_an_access(self, client, admin_user, patient_b):
        """Paciente de otra clínica: no se llega a leer nada."""
        client.force_login(admin_user)
        response = client.get(reverse('patients:detail', kwargs={'id': patient_b.pk}))
        assert response.status_code == 404
        assert not AccessLog.objects.exists()


@pytest.mark.django_db
class TestApiViewSet:
    def test_retrieve_logs_the_patient(self, admin_client, admin_user, patient_a):
        response = admin_client.get(f'/api/patients/{patient_a.pk}/')
        assert response.status_code == 200

        log = AccessLog.objects.get()
        assert log.action == AccessLog.Action.VIEW
        assert log.patient_id == patient_a.pk
        assert log.user_id == admin_user.pk
        assert log.origin == ORIGIN_API

    def test_list_logs_the_result_count(self, admin_client, patient_a):
        response = admin_client.get('/api/patients/')
        assert response.status_code == 200

        log = AccessLog.objects.get()
        assert log.action == AccessLog.Action.LIST
        assert log.result_count == 1

    def test_search_is_logged_as_search(self, admin_client, patient_a):
        admin_client.get('/api/patients/', {'search': 'Doe'})

        log = AccessLog.objects.get()
        assert log.action == AccessLog.Action.SEARCH

    def test_export_is_logged_as_export(self, admin_client, patient_a):
        response = admin_client.get('/api/patients/export/')
        assert response.status_code == 200

        log = AccessLog.objects.get()
        assert log.action == AccessLog.Action.EXPORT
        assert log.result_count == 1

    def test_agent_read_is_attributed_to_n8n(self, api_client, clinic_a, patient_a):
        api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        response = api_client.get(f'/api/patients/{patient_a.pk}/')
        assert response.status_code == 200

        log = AccessLog.objects.get()
        assert log.user is None
        assert log.user_repr == f'n8n:{clinic_a.pk}'

    def test_a_write_does_not_generate_an_access_log(self, admin_client, patient_a):
        """Las escrituras son cosa de `ChangeLog`; no deben duplicarse aquí."""
        response = admin_client.patch(
            f'/api/patients/{patient_a.pk}/', {'first_name': 'Johnny'}, format='json'
        )
        assert response.status_code == 200
        assert not AccessLog.objects.exists()


@pytest.mark.django_db
class TestLowLevelHelper:
    def test_log_access_works_without_a_request(self, patient_a):
        """La futura vista de adjuntos y cualquier tarea pueden llamarla."""
        log = log_access(
            action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
            patient=patient_a,
            obj=patient_a,
            reason='Requerimiento judicial',
        )

        assert log.pk is not None
        assert log.patient_id == patient_a.pk
        assert log.user is None
        assert log.reason == 'Requerimiento judicial'
