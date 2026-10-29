"""Límite de intentos del login, IP fiable y cierre de las otras puertas."""
import pytest
from django.test import RequestFactory
from django.urls import reverse

from audit.context import client_ip
from audit.models import AccessLog
from core import throttling
from core.models import User

LOGIN_URL = '/login/'


def attempt(client, email, password='mala', **extra):
    return client.post(LOGIN_URL, {'username': email, 'password': password}, **extra)


def logged_in(client) -> bool:
    return '_auth_user_id' in client.session


@pytest.fixture
def small_limits(settings):
    settings.LOGIN_MAX_FAILURES_PER_EMAIL = 3
    settings.LOGIN_MAX_FAILURES_PER_IP = 6


# --- Por correo ------------------------------------------------------------------


@pytest.mark.django_db
def test_correct_password_still_works_below_the_limit(client, staff_user, small_limits):
    attempt(client, staff_user.email)
    attempt(client, staff_user.email)
    response = attempt(client, staff_user.email, 'testpass123')

    assert response.status_code == 302
    assert logged_in(client)


@pytest.mark.django_db
def test_locked_email_refuses_even_the_right_password(client, staff_user, small_limits):
    for _ in range(3):
        attempt(client, staff_user.email)

    response = attempt(client, staff_user.email, 'testpass123')

    assert response.status_code == 429
    assert not logged_in(client)
    assert 'Demasiados intentos' in response.content.decode()


@pytest.mark.django_db
def test_a_locked_login_never_checks_the_password(client, staff_user, small_limits, monkeypatch):
    """Si se comprobara, el atacante bloqueado seguiría pudiendo medir aciertos."""
    for _ in range(3):
        attempt(client, staff_user.email)

    def boom(*args, **kwargs):
        raise AssertionError('se ha comprobado la contraseña de un login bloqueado')

    monkeypatch.setattr('django.contrib.auth.forms.authenticate', boom)
    response = attempt(client, staff_user.email, 'testpass123')

    assert response.status_code == 429
    html = response.content.decode()
    assert 'Demasiados intentos' in html
    assert 'clave correctos' not in html
    # El correo tecleado se conserva para no obligar a escribirlo otra vez.
    assert staff_user.email in html


@pytest.mark.django_db
def test_lock_is_per_email_not_global(client, staff_user, admin_user, small_limits):
    for _ in range(3):
        attempt(client, staff_user.email)

    response = attempt(client, admin_user.email, 'testpass123')

    assert response.status_code == 302
    assert logged_in(client)


@pytest.mark.django_db
def test_unknown_email_is_locked_the_same_way(client, small_limits):
    """El bloqueo no delata qué correos existen."""
    for _ in range(3):
        attempt(client, 'nadie@alpha.test')

    assert attempt(client, 'nadie@alpha.test').status_code == 429


@pytest.mark.django_db
def test_email_is_case_insensitive(client, staff_user, small_limits):
    for email in ('STAFF@alpha.test', 'Staff@Alpha.test', 'staff@alpha.test'):
        attempt(client, email)

    assert attempt(client, staff_user.email, 'testpass123').status_code == 429


@pytest.mark.django_db
def test_success_clears_the_email_counter(client, staff_user, small_limits):
    attempt(client, staff_user.email)
    attempt(client, staff_user.email)
    attempt(client, staff_user.email, 'testpass123')
    client.logout()

    attempt(client, staff_user.email)
    attempt(client, staff_user.email)
    # Con el contador a cero, dos fallos más no bloquean.
    assert attempt(client, staff_user.email, 'testpass123').status_code == 302


@pytest.mark.django_db
def test_empty_password_does_not_count(client, staff_user, small_limits):
    for _ in range(5):
        client.post(LOGIN_URL, {'username': staff_user.email, 'password': ''})

    assert attempt(client, staff_user.email, 'testpass123').status_code == 302


@pytest.mark.django_db
def test_lock_expires_when_the_cache_entry_does(client, staff_user, small_limits):
    for _ in range(3):
        attempt(client, staff_user.email)
    assert attempt(client, staff_user.email, 'testpass123').status_code == 429

    throttling.clear('login-email', staff_user.email)

    assert attempt(client, staff_user.email, 'testpass123').status_code == 302


# --- Por IP ----------------------------------------------------------------------


@pytest.mark.django_db
def test_ip_lock_covers_many_emails(client, staff_user, small_limits):
    for i in range(6):
        attempt(client, f'otro{i}@alpha.test', REMOTE_ADDR='10.0.0.7')

    # Desde esa IP ya no entra ni quien acierta; desde otra, sí.
    assert attempt(client, staff_user.email, 'testpass123', REMOTE_ADDR='10.0.0.7').status_code == 429
    assert attempt(client, staff_user.email, 'testpass123', REMOTE_ADDR='10.0.0.8').status_code == 302


def test_ipv6_is_counted_by_64_prefix():
    assert throttling.ip_bucket('2001:db8:1:2:aaaa:bbbb:cccc:dddd') == throttling.ip_bucket(
        '2001:db8:1:2::1'
    )
    assert throttling.ip_bucket('2001:db8:1:3::1') != throttling.ip_bucket('2001:db8:1:2::1')
    assert throttling.ip_bucket('203.0.113.9') == '203.0.113.9'


# --- La IP no se deja escoger -----------------------------------------------------


def _request(forwarded=None, remote='192.168.0.5'):
    extra = {'REMOTE_ADDR': remote}
    if forwarded is not None:
        extra['HTTP_X_FORWARDED_FOR'] = forwarded
    return RequestFactory().get('/', **extra)


def test_without_proxies_forwarded_header_is_ignored(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request('9.9.9.9, 8.8.8.8')) == '192.168.0.5'


def test_with_proxies_the_client_is_counted_from_the_right(settings):
    """Traefik → nginx: lo escrito por el cliente queda a la izquierda y no cuenta."""
    settings.TRUSTED_PROXY_COUNT = 2
    spoofed = '1.1.1.1, 203.0.113.9, 172.18.0.3'
    assert client_ip(_request(spoofed)) == '203.0.113.9'


def test_a_short_chain_falls_back_to_the_first_hop(settings):
    settings.TRUSTED_PROXY_COUNT = 2
    assert client_ip(_request('203.0.113.9')) == '203.0.113.9'


@pytest.mark.django_db
def test_spoofing_the_forwarded_header_does_not_dodge_the_ip_lock(client, staff_user, settings, small_limits):
    settings.TRUSTED_PROXY_COUNT = 1
    for i in range(6):
        # Cada intento «viene de» una IP distinta según el cliente, pero el proxy
        # añade siempre la verdadera al final.
        attempt(client, f'otro{i}@alpha.test', HTTP_X_FORWARDED_FOR=f'7.7.7.{i}, 198.51.100.4')

    response = attempt(
        client, staff_user.email, 'testpass123', HTTP_X_FORWARDED_FOR='7.7.7.99, 198.51.100.4'
    )
    assert response.status_code == 429


# --- Auditoría ---------------------------------------------------------------------


@pytest.mark.django_db
def test_failures_and_the_lock_leave_a_trail(client, staff_user, small_limits):
    for _ in range(3):
        attempt(client, staff_user.email)
    attempt(client, staff_user.email)  # rebota contra el bloqueo: no escribe nada

    failed = AccessLog.objects.filter(action=AccessLog.Action.LOGIN_FAILED)
    locked = AccessLog.objects.filter(action=AccessLog.Action.LOGIN_LOCKED)
    assert failed.count() == 3
    assert locked.count() == 1
    assert staff_user.email in failed.first().object_repr
    assert 'testpass' not in ''.join(a.object_repr for a in AccessLog.objects.all())


@pytest.mark.django_db
def test_successful_login_is_logged(client, staff_user):
    attempt(client, staff_user.email, 'testpass123')

    log = AccessLog.objects.get(action=AccessLog.Action.LOGIN)
    assert log.user == staff_user


# --- Otras puertas -----------------------------------------------------------------


@pytest.mark.django_db
def test_token_endpoint_is_throttled_too(client, staff_user, small_limits):
    for _ in range(3):
        client.post('/api/auth/token/', {'username': staff_user.email, 'password': 'mala'})

    response = client.post('/api/auth/token/', {'username': staff_user.email, 'password': 'testpass123'})

    assert response.status_code == 429


@pytest.mark.django_db
def test_token_endpoint_still_issues_tokens(client, staff_user):
    response = client.post('/api/auth/token/', {'username': staff_user.email, 'password': 'testpass123'})

    assert response.status_code == 200
    assert 'token' in response.json()


@pytest.mark.django_db
def test_admin_login_goes_through_our_login(client):
    response = client.get('/admin/login/?next=/admin/')

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('core:login'))
    assert 'next=%2Fadmin%2F' in response['Location']


@pytest.mark.django_db
def test_admin_login_does_not_loop_for_a_non_staff_user(client, staff_user):
    client.force_login(staff_user)

    assert client.get('/admin/login/').status_code == 403


@pytest.mark.django_db
def test_admin_login_form_cannot_be_posted_to(client, superuser):
    response = client.post('/admin/login/', {'username': superuser.email, 'password': 'testpass123'})

    assert response.status_code == 405
    assert not logged_in(client)


@pytest.mark.django_db
def test_inactive_user_counts_as_a_failure(client, staff_user, small_limits):
    User.objects.filter(pk=staff_user.pk).update(is_active=False)
    for _ in range(3):
        attempt(client, staff_user.email, 'testpass123')

    assert attempt(client, staff_user.email, 'testpass123').status_code == 429
