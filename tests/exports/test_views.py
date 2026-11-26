"""Quién puede pedir y descargar una exportación, y qué queda registrado."""
from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AccessLog
from exports.models import DataExport
from exports.tasks import build_export

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def staff_web(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.fixture
def create_url(patient_a):
    return reverse('exports:patient-create', args=[patient_a.pk])


@pytest.fixture
def ready(file_a, clinic_a, admin_user, patient_a):
    export = DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, requested_by_label='Admin',
        scope=DataExport.Scope.PATIENT, patient=patient_a, patient_label='John Doe',
    )
    build_export.delay(str(export.pk))
    export.refresh_from_db()
    assert export.status == DataExport.Status.READY
    return export


class TestRequesting:
    def test_an_admin_requests_and_the_task_runs(self, admin_client, create_url, patient_a, file_a,
                                                 django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.post(create_url)

        assert response.status_code == 302 and response.url == reverse('exports:list')
        export = DataExport.objects.get()
        assert export.scope == DataExport.Scope.PATIENT and export.patient_id == patient_a.pk
        assert export.requested_by_label
        assert export.status == DataExport.Status.READY

    def test_the_request_leaves_an_access_log(self, admin_client, create_url, admin_user, patient_a,
                                              django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False):
            admin_client.post(create_url)

        log = AccessLog.objects.filter(action=AccessLog.Action.EXPORT).latest('id')
        assert log.user == admin_user and log.patient_id == patient_a.pk
        assert 'Solicitud' in log.object_repr

    def test_staff_cannot(self, staff_web, create_url):
        assert staff_web.post(create_url).status_code == 403
        assert not DataExport.objects.exists()

    def test_anonymous_is_sent_to_login(self, client, create_url):
        response = client.post(create_url)
        assert response.status_code == 302 and '/login/' in response.url
        assert not DataExport.objects.exists()

    def test_the_agent_api_key_is_denied(self, client, create_url, clinic_a):
        response = client.post(create_url, HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        assert response.status_code == 302 and '/login/' in response.url
        assert not DataExport.objects.exists()

    def test_a_patient_of_another_clinic_is_a_404(self, admin_client, patient_b):
        response = admin_client.post(reverse('exports:patient-create', args=[patient_b.pk]))
        assert response.status_code == 404
        assert not DataExport.objects.exists()

    def test_get_is_not_allowed(self, admin_client, create_url):
        assert admin_client.get(create_url).status_code == 405

    def test_an_archived_patient_can_be_exported(self, admin_client, create_url, patient_a, admin_user,
                                                 django_capture_on_commit_callbacks):
        patient_a.archived_at = timezone.now()
        patient_a.archived_by = admin_user
        patient_a.save()
        with django_capture_on_commit_callbacks(execute=False):
            assert admin_client.post(create_url).status_code == 302
        assert DataExport.objects.count() == 1

    def test_a_second_request_while_one_is_running_is_refused(self, admin_client, create_url,
                                                              django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False):
            admin_client.post(create_url)
            response = admin_client.post(create_url, follow=True)

        assert DataExport.objects.count() == 1
        assert 'ya hay una exportación' in response.content.decode().lower()

    def test_a_broker_that_is_down_does_not_leave_it_pending(self, admin_client, create_url, monkeypatch,
                                                             django_capture_on_commit_callbacks):
        from exports import service

        def down(*args, **kwargs):
            raise ConnectionError('broker')

        monkeypatch.setattr(service.build_export, 'delay', down)

        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(create_url)

        export = DataExport.objects.get()
        assert export.status == DataExport.Status.FAILED and export.error
        # y no bloquea la siguiente
        monkeypatch.undo()
        with django_capture_on_commit_callbacks(execute=False):
            admin_client.post(create_url)
        assert DataExport.objects.count() == 2


class TestList:
    def test_the_list_shows_the_clinics_exports_only(self, admin_client, ready, clinic_b, admin_user_b):
        DataExport.objects.create(
            clinic=clinic_b, requested_by=admin_user_b, scope=DataExport.Scope.CLINIC,
            requested_by_label='Otro',
        )
        html = admin_client.get(reverse('exports:list')).content.decode()
        assert 'John Doe' in html and 'Otro' not in html
        assert reverse('exports:download', args=[ready.pk]) in html

    def test_the_fragment_returns_only_the_rows(self, admin_client, ready):
        response = admin_client.get(reverse('exports:list') + '?fragment=1')
        html = response.content.decode()
        assert 'id="export-rows"' in html and '<html' not in html
        assert response['Cache-Control'] == 'private, no-store'

    def test_it_polls_only_while_something_is_running(self, admin_client, ready, clinic_a, admin_user):
        assert 'every 4s' not in admin_client.get(reverse('exports:list')).content.decode()
        DataExport.objects.create(
            clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC,
        )
        assert 'hx-trigger="every 4s"' in admin_client.get(reverse('exports:list')).content.decode()

    def test_staff_cannot_see_it(self, staff_web):
        assert staff_web.get(reverse('exports:list')).status_code == 403

    def test_the_sidebar_link_is_only_for_admins(self, admin_client, client, staff_user):
        link = reverse('exports:list')
        assert f'href="{link}"' in admin_client.get(reverse('exports:list')).content.decode()

        client.force_login(staff_user)
        # la página de staff no debe ofrecer el enlace (cualquier página del panel sirve)
        assert f'href="{link}"' not in client.get(reverse('services:list')).content.decode()


class TestPatientFileButton:
    def test_the_button_is_for_admins(self, admin_client, patient_a):
        html = admin_client.get(reverse('patients:detail', args=[patient_a.pk])).content.decode()
        assert reverse('exports:patient-create', args=[patient_a.pk]) in html

    def test_not_for_staff(self, client, staff_user, patient_a):
        client.force_login(staff_user)
        html = client.get(reverse('patients:detail', args=[patient_a.pk])).content.decode()
        assert reverse('exports:patient-create', args=[patient_a.pk]) not in html


class TestDownload:
    def test_it_redirects_to_a_signed_url_and_logs_it(self, admin_client, ready, admin_user):
        before = ready.downloads

        response = admin_client.get(reverse('exports:download', args=[ready.pk]))

        assert response.status_code == 302
        assert ready.file.name in response.url
        assert response['Cache-Control'] == 'private, no-store, max-age=0'
        assert response['Referrer-Policy'] == 'no-referrer'
        log = AccessLog.objects.filter(action=AccessLog.Action.EXPORT).latest('id')
        assert log.user == admin_user and 'Descarga' in log.object_repr
        ready.refresh_from_db()
        assert ready.downloads == before + 1

    def test_the_url_is_signed_and_short_lived(self, admin_client, ready):
        from tests.clinical.test_lesion_attachments import R2_STORAGE
        from django.conf import settings

        with override_settings(STORAGES={**settings.STORAGES, 'clinical_media': R2_STORAGE}):
            response = admin_client.get(reverse('exports:download', args=[ready.pk]))
        assert 'X-Amz-Signature=' in response.url and 'X-Amz-Expires=600' in response.url

    def test_another_clinic_gets_a_404(self, client, ready, admin_user_b):
        client.force_login(admin_user_b)
        assert client.get(reverse('exports:download', args=[ready.pk])).status_code == 404

    def test_staff_gets_a_403(self, staff_web, ready):
        assert staff_web.get(reverse('exports:download', args=[ready.pk])).status_code == 403

    def test_an_expired_export_gets_a_404(self, admin_client, ready):
        ready.expires_at = timezone.now() - timedelta(minutes=1)
        ready.save()
        assert admin_client.get(reverse('exports:download', args=[ready.pk])).status_code == 404

    def test_one_that_is_not_ready_gets_a_404(self, admin_client, clinic_a, admin_user):
        export = DataExport.objects.create(clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC)
        assert admin_client.get(reverse('exports:download', args=[export.pk])).status_code == 404

    def test_anonymous_is_sent_to_login(self, client, ready):
        response = client.get(reverse('exports:download', args=[ready.pk]))
        assert response.status_code == 302 and '/login/' in response.url

    def test_the_agent_api_key_is_denied(self, client, ready, clinic_a):
        response = client.get(
            reverse('exports:download', args=[ready.pk]), HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}'
        )
        assert response.status_code == 302 and '/login/' in response.url


class TestNotification:
    """`notify` espera al commit: dentro de un test hay que ejecutar los callbacks a mano."""

    def run(self, export, capture):
        with capture(execute=True):
            build_export.delay(str(export.pk))

    @pytest.fixture
    def pending(self, file_a, clinic_a, admin_user, patient_a):
        return DataExport.objects.create(
            clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
        )

    def test_only_the_requester_is_told(self, pending, admin_user, clinic_a, django_capture_on_commit_callbacks):
        from core.models import User
        from notifications.models import Notification

        second_admin = User.objects.create_user(
            email='second@example.com', password='x', clinic=clinic_a, role=User.Role.ADMIN
        )

        self.run(pending, django_capture_on_commit_callbacks)

        notifications = Notification.objects.filter(kind=Notification.Kind.EXPORT_READY)
        assert [n.user_id for n in notifications] == [admin_user.pk]
        assert not notifications.filter(user=second_admin).exists()
        note = notifications.get()
        assert note.url == reverse('exports:list')
        assert 'John' not in note.title + note.body  # el push es genérico

    def test_a_failure_is_also_reported(self, pending, monkeypatch, django_capture_on_commit_callbacks):
        from exports import generation
        from notifications.models import Notification

        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(generation, 'build_export_zip', boom)

        self.run(pending, django_capture_on_commit_callbacks)

        assert 'fallado' in Notification.objects.get(kind=Notification.Kind.EXPORT_READY).detail

    def test_it_can_be_muted(self, pending, admin_user, django_capture_on_commit_callbacks):
        from notifications.models import Notification, NotificationPreference

        NotificationPreference.objects.create(user=admin_user, kind=Notification.Kind.EXPORT_READY, in_app=False)

        self.run(pending, django_capture_on_commit_callbacks)

        assert not Notification.objects.filter(kind=Notification.Kind.EXPORT_READY).exists()

    def test_a_user_without_notifications_for_it_does_not_break_the_export(self, pending, monkeypatch,
                                                                            django_capture_on_commit_callbacks):
        from exports import generation

        def broken(*args, **kwargs):
            raise RuntimeError('notificaciones caídas')

        monkeypatch.setattr(generation, 'notify', broken)

        self.run(pending, django_capture_on_commit_callbacks)

        pending.refresh_from_db()
        assert pending.status == DataExport.Status.READY
