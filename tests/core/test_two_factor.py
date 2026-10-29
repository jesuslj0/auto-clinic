"""Verificación en dos pasos (TOTP): flujo de login, middleware, cuenta y reseteo."""
import time

import pyotp
import pytest
from django.contrib.sessions.models import Session
from django.urls import reverse

from audit.models import AccessLog, ChangeLog
from core import two_factor
from core.models import RecoveryCode, TOTPDevice, User

PASSWORD = 'testpass123'


@pytest.fixture(autouse=True)
def two_factor_on(settings):
    settings.TWO_FACTOR_REQUIRED_FOR_ROLES = True


def password_login(client, user, password=PASSWORD, **extra):
    return client.post('/login/', {'username': user.email, 'password': password}, **extra)


def logged_in(client) -> bool:
    return '_auth_user_id' in client.session


def totp_for(user, offset=0):
    device = TOTPDevice.objects.get(user=user)
    secret = two_factor.decrypt_secret(device.secret)
    return pyotp.TOTP(secret).at(int(time.time()) + offset * 30)


def enroll(client, user):
    """Contraseña → configurar → primer código. Devuelve la respuesta con los códigos."""
    password_login(client, user)
    client.get(reverse('core:two-factor-setup'))
    return client.post(reverse('core:two-factor-setup'), {'code': totp_for(user)})


def recovery_codes_in(response) -> list[str]:
    return [code for code in response.context['codes']]


@pytest.fixture
def enrolled_admin(client, admin_user):
    enroll(client, admin_user)
    client.logout()
    return admin_user


# --- Primer alta, antes de entrar ------------------------------------------------


@pytest.mark.django_db
def test_password_alone_does_not_open_a_session(client, admin_user):
    response = password_login(client, admin_user)

    assert response.status_code == 302
    assert response['Location'] == reverse('core:two-factor-setup')
    assert not logged_in(client)
    # Y con eso no se llega a ninguna parte del panel.
    assert client.get('/').status_code == 302
    assert '/login/' in client.get('/')['Location']


@pytest.mark.django_db
def test_setup_page_shows_a_local_qr_and_the_manual_key(client, admin_user):
    password_login(client, admin_user)
    response = client.get(reverse('core:two-factor-setup'))

    html = response.content.decode()
    assert response.status_code == 200
    assert '<svg' in html
    assert response.context['manual_key']
    assert 'no-store' in response['Cache-Control']
    # `same-origin`, no `no-referrer`: este último hace que el navegador mande
    # `Origin: null` en el POST del formulario y CSRF lo rechace.
    assert response['Referrer-Policy'] == 'same-origin'
    # Nada del QR sale a ningún servicio externo.
    assert 'api.qrserver' not in html and 'chart.googleapis' not in html


@pytest.mark.django_db
def test_setup_page_keeps_the_same_qr_on_reload(client, admin_user):
    password_login(client, admin_user)
    first = client.get(reverse('core:two-factor-setup')).context['manual_key']
    second = client.get(reverse('core:two-factor-setup')).context['manual_key']

    assert first == second


@pytest.mark.django_db
def test_wrong_first_code_does_not_log_in_or_confirm(client, admin_user):
    password_login(client, admin_user)
    client.get(reverse('core:two-factor-setup'))
    response = client.post(reverse('core:two-factor-setup'), {'code': '000000'})

    assert response.status_code == 200
    assert 'no es correcto' in response.content.decode()
    assert not logged_in(client)
    assert not TOTPDevice.objects.get(user=admin_user).is_confirmed


@pytest.mark.django_db
def test_right_first_code_confirms_logs_in_and_shows_recovery_codes(client, admin_user):
    response = enroll(client, admin_user)

    assert response.status_code == 200
    assert logged_in(client)
    assert client.session.get(two_factor.VERIFIED_KEY) is True
    assert TOTPDevice.objects.get(user=admin_user).is_confirmed
    codes = recovery_codes_in(response)
    assert len(codes) == two_factor.RECOVERY_CODE_COUNT
    assert len(set(codes)) == len(codes)
    assert RecoveryCode.objects.filter(user=admin_user).count() == two_factor.RECOVERY_CODE_COUNT
    # La página sigue funcionando para entrar: el panel responde.
    assert client.get('/').status_code == 200


@pytest.mark.django_db
def test_secret_is_stored_encrypted_and_codes_only_as_hashes(client, admin_user):
    response = enroll(client, admin_user)

    device = TOTPDevice.objects.get(user=admin_user)
    plain = two_factor.decrypt_secret(device.secret)
    assert plain not in device.secret
    assert len(plain) == 32
    for code in recovery_codes_in(response):
        raw = code.replace('-', '')
        assert not RecoveryCode.objects.filter(code_hash=raw).exists()
        assert not RecoveryCode.objects.filter(code_hash__icontains=raw).exists()


# --- Login con segundo factor ------------------------------------------------------


@pytest.mark.django_db
def test_second_login_asks_for_the_code(client, enrolled_admin):
    response = password_login(client, enrolled_admin)

    assert response['Location'] == reverse('core:two-factor-verify')
    assert not logged_in(client)


@pytest.mark.django_db
def test_wrong_code_is_refused(client, enrolled_admin):
    password_login(client, enrolled_admin)
    response = client.post(reverse('core:two-factor-verify'), {'code': '123456'})

    assert response.status_code == 200
    assert 'no es correcto' in response.content.decode()
    assert not logged_in(client)


@pytest.mark.django_db
def test_right_code_opens_the_session_and_follows_next(client, enrolled_admin):
    client.post('/login/?next=/pacientes/', {'username': enrolled_admin.email, 'password': PASSWORD})
    # El código de ese mismo intervalo ya se usó al configurar: se usa el siguiente.
    response = client.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_admin, 1)})

    assert response.status_code == 302
    assert response['Location'] == '/pacientes/'
    assert logged_in(client)
    assert client.session.get(two_factor.VERIFIED_KEY) is True


@pytest.mark.django_db
def test_a_code_cannot_be_used_twice(client, enrolled_admin):
    code = totp_for(enrolled_admin, 1)
    password_login(client, enrolled_admin)
    assert client.post(reverse('core:two-factor-verify'), {'code': code}).status_code == 302
    client.logout()

    password_login(client, enrolled_admin)
    response = client.post(reverse('core:two-factor-verify'), {'code': code})

    assert response.status_code == 200
    assert not logged_in(client)


@pytest.mark.django_db
def test_clock_drift_of_one_step_is_tolerated_but_not_two(client, enrolled_admin):
    password_login(client, enrolled_admin)
    assert client.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_admin, 2)}).status_code == 200
    assert not logged_in(client)


@pytest.mark.django_db
def test_spaces_in_the_code_are_ignored(client, enrolled_admin):
    password_login(client, enrolled_admin)
    code = totp_for(enrolled_admin, 1)
    response = client.post(reverse('core:two-factor-verify'), {'code': f'{code[:3]} {code[3:]}'})

    assert response.status_code == 302


@pytest.mark.django_db
def test_verify_page_without_a_pending_login_goes_to_login(client):
    assert client.get(reverse('core:two-factor-verify')).status_code == 302
    assert client.post(reverse('core:two-factor-verify'), {'code': '123456'}).status_code == 302


@pytest.mark.django_db
def test_pending_login_expires(client, enrolled_admin, settings):
    password_login(client, enrolled_admin)
    session = client.session
    session[two_factor.PENDING_KEY]['expires'] = time.time() - 1
    session.save()

    response = client.get(reverse('core:two-factor-verify'))

    assert response.status_code == 302
    assert response['Location'] == reverse('core:login')


@pytest.mark.django_db
def test_too_many_wrong_codes_lock_and_drop_the_pending_login(client, enrolled_admin, settings):
    settings.TWO_FACTOR_MAX_FAILURES = 3
    password_login(client, enrolled_admin)
    for _ in range(2):
        client.post(reverse('core:two-factor-verify'), {'code': '000000'})
    response = client.post(reverse('core:two-factor-verify'), {'code': '000000'})

    assert response.status_code == 429
    # Ni el código bueno entra ya: el login pendiente se descartó.
    assert client.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_admin, 1)}).status_code == 302
    assert not logged_in(client)
    # Y volver a empezar con la contraseña tampoco, mientras dure el bloqueo.
    password_login(client, enrolled_admin)
    response = client.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_admin, 1)})
    assert response.status_code == 429
    assert not logged_in(client)


@pytest.mark.django_db
def test_failed_codes_leave_a_trail_attributed_to_the_user(client, enrolled_admin, settings):
    settings.TWO_FACTOR_MAX_FAILURES = 2
    password_login(client, enrolled_admin)
    client.post(reverse('core:two-factor-verify'), {'code': '000000'})
    client.post(reverse('core:two-factor-verify'), {'code': '000000'})

    assert AccessLog.objects.filter(
        action=AccessLog.Action.LOGIN_FAILED, user=enrolled_admin
    ).count() == 2
    assert AccessLog.objects.filter(
        action=AccessLog.Action.LOGIN_LOCKED, user=enrolled_admin
    ).count() == 1


# --- Códigos de recuperación ---------------------------------------------------------


@pytest.mark.django_db
def test_recovery_code_logs_in_once(client, admin_user):
    codes = recovery_codes_in(enroll(client, admin_user))
    client.logout()

    password_login(client, admin_user)
    response = client.post(reverse('core:two-factor-verify'), {'code': codes[0]})
    assert response.status_code == 302
    assert logged_in(client)
    assert two_factor.remaining_recovery_codes(admin_user) == two_factor.RECOVERY_CODE_COUNT - 1
    client.logout()

    password_login(client, admin_user)
    response = client.post(reverse('core:two-factor-verify'), {'code': codes[0]})
    assert response.status_code == 200
    assert not logged_in(client)


@pytest.mark.django_db
def test_recovery_code_is_case_and_dash_insensitive(client, admin_user):
    codes = recovery_codes_in(enroll(client, admin_user))
    client.logout()

    password_login(client, admin_user)
    response = client.post(
        reverse('core:two-factor-verify'), {'code': codes[3].lower().replace('-', ' ')}
    )

    assert response.status_code == 302


@pytest.mark.django_db
def test_using_a_recovery_code_is_logged_and_warned(client, admin_user):
    codes = recovery_codes_in(enroll(client, admin_user))
    client.logout()
    password_login(client, admin_user)
    client.post(reverse('core:two-factor-verify'), {'code': codes[0]})

    assert AccessLog.objects.filter(
        action=AccessLog.Action.TWO_FACTOR_CHANGE, user=admin_user,
        object_repr__contains='código de recuperación',
    ).exists()
    shown = [str(m) for m in client.get('/').context['messages']]
    assert any('código de recuperación' in m for m in shown)


# --- Quién la necesita -------------------------------------------------------------


@pytest.mark.django_db
def test_professionals_need_it_too(client, staff_user):
    assert getattr(staff_user, 'professional_profile', None) is not None
    response = password_login(client, staff_user)

    assert response['Location'] == reverse('core:two-factor-setup')
    assert not logged_in(client)


@pytest.mark.django_db
def test_a_plain_user_logs_in_with_just_the_password(client, clinic_a):
    plain = User.objects.create_user(
        email='plain@alpha.test', password=PASSWORD, clinic=None, role=User.Role.STAFF
    )
    assert not two_factor.requires_two_factor(plain)

    assert password_login(client, plain)['Location'] == '/'
    assert logged_in(client)


@pytest.mark.django_db
def test_a_user_with_a_device_always_uses_it_even_if_the_role_does_not_ask(client, enrolled_admin, settings):
    settings.TWO_FACTOR_REQUIRED_FOR_ROLES = False

    response = password_login(client, enrolled_admin)

    assert response['Location'] == reverse('core:two-factor-verify')


@pytest.mark.django_db
def test_an_unconfirmed_device_does_not_block_anyone(client, clinic_a):
    plain = User.objects.create_user(email='plain@alpha.test', password=PASSWORD, clinic=None)
    two_factor.start_enrollment(plain)

    assert not two_factor.requires_two_factor(plain)
    assert password_login(client, plain)['Location'] == '/'


# --- Red de seguridad: sesiones sin verificar ----------------------------------------


@pytest.mark.django_db
def test_middleware_logs_out_an_unverified_session(client, admin_user):
    client.force_login(admin_user)

    response = client.get('/')

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('core:login'))
    assert not logged_in(client)


@pytest.mark.django_db
def test_middleware_answers_403_json_on_the_api(client, admin_user):
    client.force_login(admin_user)

    response = client.get('/api/patients/')

    assert response.status_code == 403
    assert not logged_in(client)


@pytest.mark.django_db
def test_middleware_lets_a_verified_session_through(client, admin_user):
    client.force_login(admin_user)
    session = client.session
    session[two_factor.VERIFIED_KEY] = True
    session.save()

    assert client.get('/').status_code == 200
    assert logged_in(client)


@pytest.mark.django_db
def test_middleware_does_not_touch_users_who_do_not_need_it(client, clinic_a):
    plain = User.objects.create_user(email='plain@alpha.test', password=PASSWORD, clinic=None)
    client.force_login(plain)

    assert client.get('/').status_code == 200


@pytest.mark.django_db
def test_agent_api_key_is_not_affected(admin_user):
    from rest_framework.test import APIClient

    from core.models import Clinic

    clinic = Clinic.objects.get(clinic_id=admin_user.clinic_id)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic.agent_api_key}')

    assert api.get('/api/agent/profile/').status_code == 200


@pytest.mark.django_db
def test_admin_site_login_cannot_skip_the_second_factor(client, superuser):
    client.force_login(superuser)

    response = client.get('/admin/')

    assert response.status_code == 302
    assert not logged_in(client)


@pytest.mark.django_db(transaction=True)
async def test_websocket_refuses_an_unverified_session(admin_user):
    from channels.testing import WebsocketCommunicator

    from appointments.consumers import AppointmentConsumer
    from core.consumers import CLOSE_UNAUTHENTICATED

    communicator = WebsocketCommunicator(AppointmentConsumer.as_asgi(), '/ws/appointments/')
    communicator.scope['user'] = admin_user
    communicator.scope['session'] = {}
    await communicator.connect()

    output = await communicator.receive_output(timeout=1)
    assert output['type'] == 'websocket.close'
    assert output['code'] == CLOSE_UNAUTHENTICATED


@pytest.mark.django_db(transaction=True)
async def test_websocket_accepts_a_verified_session(admin_user):
    from channels.testing import WebsocketCommunicator

    from appointments.consumers import AppointmentConsumer

    communicator = WebsocketCommunicator(AppointmentConsumer.as_asgi(), '/ws/appointments/')
    communicator.scope['user'] = admin_user
    communicator.scope['session'] = {two_factor.VERIFIED_KEY: True}
    connected, _ = await communicator.connect()

    assert connected is True
    await communicator.disconnect()


# --- Mi cuenta → Seguridad -------------------------------------------------------------


def verified_client(client, user):
    """Un cliente dentro y verificado, sin pasar por el login."""
    client.force_login(user)
    session = client.session
    session[two_factor.VERIFIED_KEY] = True
    session.save()
    return client


def reauth(user, code=None, password=PASSWORD):
    return {'password': password, 'code': code or totp_for(user, 1)}


@pytest.mark.django_db
def test_security_tab_shows_the_state(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    response = client.get(reverse('core:account-security'))

    assert response.status_code == 200
    assert response.context['two_factor_active'] is True
    assert response.context['two_factor_required'] is True
    assert response.context['recovery_codes_left'] == two_factor.RECOVERY_CODE_COUNT
    assert 'Desactivar la verificación' not in response.content.decode()


@pytest.mark.django_db
def test_a_user_can_opt_in_from_the_account(client, clinic_a):
    plain = User.objects.create_user(email='plain@alpha.test', password=PASSWORD, clinic=None)
    client.force_login(plain)

    assert client.get(reverse('core:account-security')).context['two_factor_active'] is False
    assert client.get(reverse('core:two-factor-account-setup')).status_code == 200
    response = client.post(reverse('core:two-factor-account-setup'), {'code': totp_for(plain)})

    assert response.status_code == 200
    assert len(recovery_codes_in(response)) == two_factor.RECOVERY_CODE_COUNT
    assert two_factor.has_confirmed_device(plain)
    # Sigue dentro: la marca de verificada se puso al activar.
    assert client.get('/').status_code == 200


@pytest.mark.django_db
def test_regenerating_codes_needs_password_and_code(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    old = set(RecoveryCode.objects.filter(user=enrolled_admin).values_list('code_hash', flat=True))

    wrong = client.post(reverse('core:two-factor-recovery-codes'), reauth(enrolled_admin, '000000'))
    assert wrong.status_code == 302
    assert set(RecoveryCode.objects.filter(user=enrolled_admin).values_list('code_hash', flat=True)) == old

    bad_password = client.post(
        reverse('core:two-factor-recovery-codes'), reauth(enrolled_admin, password='otra')
    )
    assert bad_password.status_code == 302
    assert set(RecoveryCode.objects.filter(user=enrolled_admin).values_list('code_hash', flat=True)) == old

    ok = client.post(reverse('core:two-factor-recovery-codes'), reauth(enrolled_admin))
    assert ok.status_code == 200
    new = set(RecoveryCode.objects.filter(user=enrolled_admin).values_list('code_hash', flat=True))
    assert len(new) == two_factor.RECOVERY_CODE_COUNT
    assert not (new & old)


@pytest.mark.django_db
def test_old_recovery_codes_stop_working_after_regeneration(client, admin_user):
    old_codes = recovery_codes_in(enroll(client, admin_user))
    verified = client
    verified.post(reverse('core:two-factor-recovery-codes'), reauth(admin_user))
    client.logout()

    password_login(client, admin_user)
    response = client.post(reverse('core:two-factor-verify'), {'code': old_codes[0]})

    assert response.status_code == 200
    assert not logged_in(client)


@pytest.mark.django_db
def test_required_roles_cannot_disable_it(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    client.post(reverse('core:two-factor-disable'), reauth(enrolled_admin))

    assert two_factor.has_confirmed_device(enrolled_admin)


@pytest.mark.django_db
def test_an_optional_user_can_disable_it_with_password_and_code(client, clinic_a, settings):
    plain = User.objects.create_user(email='plain@alpha.test', password=PASSWORD, clinic=None)
    client.force_login(plain)
    client.get(reverse('core:two-factor-account-setup'))
    client.post(reverse('core:two-factor-account-setup'), {'code': totp_for(plain)})

    client.post(reverse('core:two-factor-disable'), reauth(plain, '000000'))
    assert two_factor.has_confirmed_device(plain)

    client.post(reverse('core:two-factor-disable'), reauth(plain))
    assert not TOTPDevice.objects.filter(user=plain).exists()
    assert not RecoveryCode.objects.filter(user=plain).exists()


@pytest.mark.django_db
def test_changing_device_replaces_the_secret_after_confirming(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    old_secret = TOTPDevice.objects.get(user=enrolled_admin).secret

    response = client.post(reverse('core:two-factor-change-device'), reauth(enrolled_admin))
    assert response['Location'] == reverse('core:two-factor-account-setup')
    device = TOTPDevice.objects.get(user=enrolled_admin)
    assert device.secret != old_secret
    assert not device.is_confirmed

    page = client.post(reverse('core:two-factor-account-setup'), {'code': totp_for(enrolled_admin)})
    assert page.status_code == 200
    assert TOTPDevice.objects.get(user=enrolled_admin).is_confirmed


@pytest.mark.django_db
def test_wrong_reauth_counts_towards_the_lock(client, enrolled_admin, settings):
    settings.TWO_FACTOR_MAX_FAILURES = 2
    verified_client(client, enrolled_admin)
    for _ in range(2):
        client.post(reverse('core:two-factor-recovery-codes'), reauth(enrolled_admin, '000000'))

    # Bloqueado: ni con todo correcto.
    client.post(reverse('core:two-factor-recovery-codes'), reauth(enrolled_admin))
    assert two_factor.remaining_recovery_codes(enrolled_admin) == two_factor.RECOVERY_CODE_COUNT
    assert RecoveryCode.objects.filter(user=enrolled_admin).count() == two_factor.RECOVERY_CODE_COUNT


@pytest.mark.django_db
def test_security_actions_require_login(client):
    for name in ('two-factor-recovery-codes', 'two-factor-change-device', 'two-factor-disable'):
        response = client.post(reverse(f'core:{name}'), {'password': 'x', 'code': 'y'})
        assert response.status_code == 302 and '/login/' in response['Location'], name


# --- Reseteo por un administrador -----------------------------------------------------


@pytest.fixture
def enrolled_staff(client, staff_user):
    enroll(client, staff_user)
    client.logout()
    return staff_user


@pytest.mark.django_db
def test_admin_can_reset_a_colleague(client, enrolled_admin, enrolled_staff):
    # El colega tiene una sesión abierta.
    other = type(client)()
    password_login(other, enrolled_staff)
    other.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_staff, 1)})
    assert logged_in(other)

    verified_client(client, enrolled_admin)
    response = client.post(
        reverse('core:two-factor-reset', args=[enrolled_staff.pk]), {'next': '/profesionales/'}
    )

    assert response.status_code == 302
    assert response['Location'] == '/profesionales/'
    assert not TOTPDevice.objects.filter(user=enrolled_staff).exists()
    assert not RecoveryCode.objects.filter(user=enrolled_staff).exists()
    # Sus sesiones se cerraron y, al volver, configura de nuevo.
    assert not logged_in(other)
    assert password_login(other, enrolled_staff)['Location'] == reverse('core:two-factor-setup')
    assert AccessLog.objects.filter(
        action=AccessLog.Action.TWO_FACTOR_CHANGE, user=enrolled_admin,
        object_repr__contains='Reseteada por',
    ).exists()


@pytest.mark.django_db
def test_reset_leaves_other_peoples_sessions_alone(client, enrolled_admin, enrolled_staff):
    verified_client(client, enrolled_admin)
    before = Session.objects.count()

    client.post(reverse('core:two-factor-reset', args=[enrolled_staff.pk]))

    assert logged_in(client)
    assert Session.objects.count() == before


@pytest.mark.django_db
def test_admin_cannot_reset_themselves(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    client.post(reverse('core:two-factor-reset', args=[enrolled_admin.pk]))

    assert two_factor.has_confirmed_device(enrolled_admin)


@pytest.mark.django_db
def test_staff_cannot_reset_anyone(client, enrolled_admin, enrolled_staff):
    verified_client(client, enrolled_staff)

    response = client.post(reverse('core:two-factor-reset', args=[enrolled_admin.pk]))

    assert response.status_code == 403
    assert two_factor.has_confirmed_device(enrolled_admin)


@pytest.mark.django_db
def test_admin_cannot_reset_another_clinic(client, enrolled_staff, admin_user_b):
    enroll(client, admin_user_b)
    verified_client(client, admin_user_b)

    response = client.post(reverse('core:two-factor-reset', args=[enrolled_staff.pk]))

    assert response.status_code == 404
    assert two_factor.has_confirmed_device(enrolled_staff)


@pytest.mark.django_db
def test_reset_is_post_only(client, enrolled_admin, enrolled_staff):
    verified_client(client, enrolled_admin)

    assert client.get(reverse('core:two-factor-reset', args=[enrolled_staff.pk])).status_code == 405


@pytest.mark.django_db
def test_professional_edit_page_offers_the_reset(client, enrolled_admin, enrolled_staff):
    verified_client(client, enrolled_admin)
    professional = enrolled_staff.professional_profile

    html = client.get(reverse('professionals:edit', args=[professional.pk])).content.decode()

    assert reverse('core:two-factor-reset', args=[enrolled_staff.pk]) in html


@pytest.mark.django_db
def test_professional_edit_page_does_not_offer_resetting_yourself(client, enrolled_admin):
    verified_client(client, enrolled_admin)
    professional = enrolled_admin.professional_profile

    html = client.get(reverse('professionals:edit', args=[professional.pk])).content.decode()

    assert reverse('core:two-factor-reset', args=[enrolled_admin.pk]) not in html


# --- Auditoría ------------------------------------------------------------------------


@pytest.mark.django_db
def test_enrollment_is_audited_without_the_secret(client, admin_user):
    enroll(client, admin_user)

    device = TOTPDevice.objects.get(user=admin_user)
    plain = two_factor.decrypt_secret(device.secret)
    logs = ChangeLog.objects.filter(model_label='core.TOTPDevice')
    assert logs.exists()
    dump = str([log.changes for log in logs]) + ''.join(log.object_repr for log in logs)
    assert plain not in dump
    assert device.secret not in dump
    assert all(log.user == admin_user for log in logs)
    assert AccessLog.objects.filter(
        action=AccessLog.Action.TWO_FACTOR_CHANGE, user=admin_user,
        object_repr='Activó la verificación en dos pasos',
    ).exists()


@pytest.mark.django_db
def test_recovery_hashes_never_reach_the_log(client, admin_user):
    enroll(client, admin_user)

    hashes = list(RecoveryCode.objects.values_list('code_hash', flat=True))
    dump = str(list(ChangeLog.objects.filter(model_label='core.RecoveryCode').values_list('changes', flat=True)))
    assert hashes and all(h not in dump for h in hashes)


@pytest.mark.django_db
def test_consuming_a_code_step_does_not_flood_the_change_log(client, enrolled_admin):
    before = ChangeLog.objects.filter(model_label='core.TOTPDevice').count()
    password_login(client, enrolled_admin)
    client.post(reverse('core:two-factor-verify'), {'code': totp_for(enrolled_admin, 1)})

    assert ChangeLog.objects.filter(model_label='core.TOTPDevice').count() == before


# --- Piezas sueltas -------------------------------------------------------------------


@pytest.mark.django_db
def test_provisioning_uri_uses_the_standard_defaults(admin_user, settings):
    settings.TWO_FACTOR_ISSUER = 'Auto Clinic'
    device = two_factor.start_enrollment(admin_user)

    uri = two_factor.provisioning_uri(device)

    assert uri.startswith('otpauth://totp/')
    assert 'issuer=Auto+Clinic' in uri or 'issuer=Auto%20Clinic' in uri
    assert admin_user.email.replace('@', '%40') in uri
    # Sin parámetros no estándar: Google Authenticator ignoraría otros.
    assert 'algorithm' not in uri and 'digits' not in uri and 'period' not in uri


def test_encryption_key_can_be_rotated(settings):
    from cryptography.fernet import Fernet

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.TWO_FACTOR_ENCRYPTION_KEY = old
    token = two_factor.encrypt_secret('JBSWY3DPEHPK3PXP')

    settings.TWO_FACTOR_ENCRYPTION_KEY = f'{new},{old}'
    assert two_factor.decrypt_secret(token) == 'JBSWY3DPEHPK3PXP'
    assert two_factor.decrypt_secret(two_factor.encrypt_secret('X')) == 'X'

    settings.TWO_FACTOR_ENCRYPTION_KEY = new
    with pytest.raises(ValueError):
        two_factor.decrypt_secret(token)


def test_without_a_key_the_secret_is_still_encrypted(settings):
    settings.TWO_FACTOR_ENCRYPTION_KEY = ''
    token = two_factor.encrypt_secret('JBSWY3DPEHPK3PXP')

    assert 'JBSWY3DPEHPK3PXP' not in token
    assert two_factor.decrypt_secret(token) == 'JBSWY3DPEHPK3PXP'


@pytest.mark.django_db
def test_deleting_a_user_removes_their_factor(admin_user, client):
    enroll(client, admin_user)

    admin_user.delete()

    assert not TOTPDevice.objects.exists()
    assert not RecoveryCode.objects.exists()
