"""Exportar la clínica entera: contraseña + código, y con 2FA activo."""
import time

import pyotp
import pytest
from django.urls import reverse

from audit.models import AccessLog
from core import two_factor
from exports.models import DataExport

PASSWORD = 'testpass123'

pytestmark = pytest.mark.django_db


def activate_two_factor(user):
    device = two_factor.start_enrollment(user)
    two_factor.confirm_device(device)
    return pyotp.TOTP(two_factor.decrypt_secret(device.secret))


def code(totp, step=0):
    """Un código vigente. Cada uno vale una sola vez: otro paso para el siguiente intento."""
    return totp.at(int(time.time()) + step * 30)


@pytest.fixture
def url():
    return reverse('exports:clinic-create')


@pytest.fixture
def admin_2fa(client, admin_user):
    totp = activate_two_factor(admin_user)
    client.force_login(admin_user)
    session = client.session
    session[two_factor.VERIFIED_KEY] = True
    session.save()
    return client, totp


class TestGate:
    def test_without_two_factor_it_explains_and_cannot_continue(self, client, admin_user, url):
        client.force_login(admin_user)

        page = client.get(url)

        assert page.status_code == 200
        html = page.content.decode()
        assert 'Activa la verificación en dos pasos' in html
        assert reverse('core:account-security') in html
        assert 'name="code"' not in html

        response = client.post(url, {'password': PASSWORD, 'code': '123456'})
        assert response.status_code == 403
        assert not DataExport.objects.exists()

    def test_password_alone_is_not_enough(self, admin_2fa, url):
        client, totp = admin_2fa
        response = client.post(url, {'password': PASSWORD, 'code': ''})
        assert response.status_code == 200 and not DataExport.objects.exists()

    def test_a_wrong_code_creates_nothing(self, admin_2fa, url):
        client, _ = admin_2fa
        response = client.post(url, {'password': PASSWORD, 'code': '000000'})
        assert response.status_code == 200
        assert 'no son correctos' in response.content.decode()
        assert not DataExport.objects.exists()

    def test_a_wrong_password_creates_nothing(self, admin_2fa, url):
        client, totp = admin_2fa
        client.post(url, {'password': 'otra', 'code': code(totp)})
        assert not DataExport.objects.exists()

    def test_good_credentials_create_and_queue_it(self, admin_2fa, url, clinic_a, admin_user,
                                                  django_capture_on_commit_callbacks):
        client, totp = admin_2fa

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, {'password': PASSWORD, 'code': code(totp)})

        assert response.status_code == 302 and response.url == reverse('exports:list')
        export = DataExport.objects.get()
        assert export.scope == DataExport.Scope.CLINIC and export.patient_id is None
        assert export.clinic_id == clinic_a.pk and export.requested_by == admin_user
        assert export.status == DataExport.Status.READY

    def test_a_recovery_code_works_too(self, admin_2fa, url, admin_user, django_capture_on_commit_callbacks):
        client, _ = admin_2fa
        recovery = two_factor.generate_recovery_codes(admin_user)[0]
        with django_capture_on_commit_callbacks(execute=False):
            client.post(url, {'password': PASSWORD, 'code': recovery})
        assert DataExport.objects.count() == 1

    def test_failures_count_towards_the_shared_lock(self, admin_2fa, url, settings):
        client, totp = admin_2fa
        settings.TWO_FACTOR_MAX_FAILURES = 2
        for _ in range(2):
            client.post(url, {'password': PASSWORD, 'code': '000000'})

        # Con el bloqueo puesto, ni unas credenciales buenas pasan.
        response = client.post(url, {'password': PASSWORD, 'code': code(totp)})

        assert response.status_code == 200 and not DataExport.objects.exists()

    def test_a_second_request_while_one_runs_is_refused(self, admin_2fa, url, django_capture_on_commit_callbacks):
        client, totp = admin_2fa
        with django_capture_on_commit_callbacks(execute=False):
            client.post(url, {'password': PASSWORD, 'code': code(totp)})
            response = client.post(url, {'password': PASSWORD, 'code': code(totp, 1)}, follow=True)

        assert DataExport.objects.count() == 1
        assert 'ya hay una exportación' in response.content.decode().lower()

    def test_the_form_page_says_so_when_one_is_running(self, admin_2fa, url, clinic_a, admin_user):
        client, _ = admin_2fa
        DataExport.objects.create(clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC)
        html = client.get(url).content.decode()
        assert 'Ya hay una exportación de la clínica en marcha' in html and 'name="code"' not in html

    def test_the_request_is_logged(self, admin_2fa, url, admin_user, django_capture_on_commit_callbacks):
        client, totp = admin_2fa
        with django_capture_on_commit_callbacks(execute=False):
            client.post(url, {'password': PASSWORD, 'code': code(totp)})
        log = AccessLog.objects.filter(action=AccessLog.Action.EXPORT).latest('id')
        assert log.user == admin_user and 'toda la clínica' in log.object_repr


class TestWho:
    def test_staff_is_refused(self, client, staff_user, url):
        client.force_login(staff_user)
        assert client.get(url).status_code == 403
        assert client.post(url, {}).status_code == 403

    def test_anonymous_is_sent_to_login(self, client, url):
        response = client.get(url)
        assert response.status_code == 302 and '/login/' in response.url

    def test_the_agent_api_key_is_denied(self, client, url, clinic_a):
        response = client.post(url, {}, HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        assert response.status_code == 302 and '/login/' in response.url

    def test_a_superuser_without_a_clinic_cannot(self, client, superuser, url):
        client.force_login(superuser)
        assert client.get(url).status_code == 403

    def test_it_never_leaks_the_referrer_nor_is_cached(self, admin_2fa, url):
        client, _ = admin_2fa
        response = client.get(url)
        # `same-origin`, NUNCA `no-referrer`: con el segundo el navegador manda `Origin: null`
        # en el POST del formulario y CSRF lo rechaza.
        assert response['Referrer-Policy'] == 'same-origin'
        assert 'no-store' in response['Cache-Control']

    def test_the_button_is_on_the_list(self, admin_2fa):
        client, _ = admin_2fa
        assert reverse('exports:clinic-create') in client.get(reverse('exports:list')).content.decode()
