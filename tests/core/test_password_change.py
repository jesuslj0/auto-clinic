"""Cambio de la propia contraseña desde «Mi cuenta».

Lo que se defiende aquí, además de que el cambio funcione: que la contraseña
nueva pase por los validadores, que acertarla no te eche de la sesión, que el
cambio quede auditado sin que la contraseña aparezca en ninguna parte, y que
nadie pueda sentarse en una sesión ajena a probar contraseñas indefinidamente.
"""
import pytest
from django.urls import reverse

from audit.models import AccessLog, ChangeLog
from core.views import PASSWORD_CHANGE_MAX_ATTEMPTS

#: La de las fixtures de `conftest.py`.
CURRENT = 'testpass123'
#: Válida para los cuatro validadores: 15 caracteres, no común, no numérica y
#: sin parecido con `admin@alpha.test`.
NEW = 'Roble-Marino42'

ACCOUNT_URL = '/cuenta/'
CHANGE_URL = '/cuenta/password/'


def post_change(client, *, old=CURRENT, new1=NEW, new2=None, htmx=False):
    extra = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    return client.post(
        reverse('core:password-change'),
        {
            'old_password': old,
            'new_password1': new1,
            'new_password2': new1 if new2 is None else new2,
        },
        **extra,
    )


# ---------------------------------------------------------------------------
# Acceso a la página
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_account_page_works_without_professional_profile(client, staff_user):
    """Cambiar la contraseña no depende de tener ficha de profesional.

    Es el motivo de que esta pantalla viva en `core` y no en `appointments`: un
    superusuario de plataforma sin clínica no tiene ficha, y aun así entra.
    """
    client.force_login(staff_user)
    response = client.get(reverse('core:account'))

    assert response.status_code == 200
    assert 'password_form' in response.context


@pytest.mark.django_db
def test_urls_are_the_expected_ones(client, admin_user):
    """Las rutas son las publicadas, no las que reverse() decida."""
    assert reverse('core:account') == ACCOUNT_URL
    assert reverse('core:password-change') == CHANGE_URL


@pytest.mark.django_db
@pytest.mark.parametrize('url_name', ['core:account', 'core:password-change'])
def test_anonymous_is_redirected_to_login(client, url_name):
    response = client.get(reverse(url_name))

    assert response.status_code == 302
    assert '/login/' in response['Location']


@pytest.mark.django_db
def test_anonymous_cannot_post(client, admin_user):
    """Sin sesión no se llega al formulario ni por POST directo."""
    response = post_change(client)

    assert response.status_code == 302
    assert '/login/' in response['Location']
    admin_user.refresh_from_db()
    assert admin_user.check_password(CURRENT)


# ---------------------------------------------------------------------------
# El cambio en sí
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_password_is_changed_and_session_survives(client, admin_user):
    """Acertar no puede echarte: `update_session_auth_hash` lo evita."""
    client.force_login(admin_user)
    response = post_change(client)

    assert response.status_code == 302
    assert response['Location'] == reverse('core:account')

    admin_user.refresh_from_db()
    assert admin_user.check_password(NEW)
    assert not admin_user.check_password(CURRENT)

    # La sesión sigue en pie: sin `update_session_auth_hash` esto redirigiría
    # al login.
    assert client.get(reverse('core:dashboard')).status_code == 200


@pytest.mark.django_db
def test_wrong_current_password_changes_nothing(client, admin_user):
    client.force_login(admin_user)
    response = post_change(client, old='no-es-esta')

    assert response.status_code == 200
    assert 'La contraseña actual no es correcta' in response.content.decode()
    admin_user.refresh_from_db()
    assert admin_user.check_password(CURRENT)


@pytest.mark.django_db
def test_mismatched_confirmation_is_rejected(client, admin_user):
    client.force_login(admin_user)
    response = post_change(client, new2='Roble-Marino43')

    assert response.status_code == 200
    assert 'no coinciden' in response.content.decode()
    admin_user.refresh_from_db()
    assert admin_user.check_password(CURRENT)


# ---------------------------------------------------------------------------
# Validadores (AUTH_PASSWORD_VALIDATORS)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize(
    'weak, motivo',
    [
        ('Corta12', 'demasiado corta (mínimo 10)'),
        ('password123', 'demasiado común'),
        ('9182736450', 'solo dígitos'),
        ('admin@alpha.test', 'igual al correo del usuario'),
    ],
)
def test_weak_passwords_are_rejected(client, admin_user, weak, motivo):
    """Sin `AUTH_PASSWORD_VALIDATORS` en los settings, todas estas pasarían."""
    client.force_login(admin_user)
    response = post_change(client, new1=weak)

    assert response.status_code == 200, motivo
    admin_user.refresh_from_db()
    assert admin_user.check_password(CURRENT), f'aceptó una contraseña {motivo}'


@pytest.mark.django_db
def test_validator_errors_land_on_the_confirmation_field(client, admin_user):
    """Django los añade a `new_password2`, no a `new_password1`.

    Se fija por escrito porque la plantilla y los avisos dependen de ello.
    """
    client.force_login(admin_user)
    response = post_change(client, new1='Corta12', htmx=True)
    form = response.context['password_form']

    assert 'new_password2' in form.errors
    assert 'new_password1' not in form.errors


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_change_is_audited_without_leaking_the_password(client, admin_user):
    client.force_login(admin_user)
    post_change(client)

    entry = AccessLog.objects.filter(action=AccessLog.Action.PASSWORD_CHANGE).get()
    assert entry.user == admin_user

    # Ni la vieja ni la nueva pueden aparecer en ningún campo de ningún log.
    huellas = [
        str(value)
        for log in [*AccessLog.objects.all(), *ChangeLog.objects.all()]
        for value in log.__dict__.values()
    ]
    for secreto in (CURRENT, NEW):
        assert not any(secreto in huella for huella in huellas)


@pytest.mark.django_db
def test_failed_attempt_is_not_audited_as_a_change(client, admin_user):
    """Solo se registra el cambio consumado, no el intento."""
    client.force_login(admin_user)
    post_change(client, old='no-es-esta')

    assert not AccessLog.objects.filter(action=AccessLog.Action.PASSWORD_CHANGE).exists()


# ---------------------------------------------------------------------------
# Límite de intentos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_repeated_wrong_attempts_lock_the_form(client, admin_user):
    client.force_login(admin_user)
    for _ in range(PASSWORD_CHANGE_MAX_ATTEMPTS):
        post_change(client, old='no-es-esta')

    # Bloqueado: ni siquiera con la contraseña correcta.
    response = post_change(client)

    assert response.status_code == 200
    # Por el contexto y no por el texto: el aviso está siempre en el DOM y solo
    # se oculta con `hidden` (ver la cabecera de `core/_password_form.html`).
    assert response.context['locked_minutes'] > 0
    admin_user.refresh_from_db()
    assert admin_user.check_password(CURRENT)


@pytest.mark.django_db
def test_lockout_notice_is_hidden_until_it_applies(client, admin_user):
    """El aviso vive en el DOM desde el principio, oculto.

    Es lo que garantiza que sus clases existan cuando htmx repinta la tarjeta:
    Tailwind va por CDN y solo compila lo que ve en el documento.
    """
    client.force_login(admin_user)
    html = client.get(reverse('core:account')).content.decode()

    assert 'Demasiados intentos' in html
    assert 'ring-danger-line hidden' in html


@pytest.mark.django_db
def test_validator_failures_do_not_count_as_attempts(client, admin_user):
    """Equivocarse con los requisitos no es intentar adivinar la contraseña."""
    client.force_login(admin_user)
    for _ in range(PASSWORD_CHANGE_MAX_ATTEMPTS + 2):
        post_change(client, new1='Corta12')

    response = post_change(client)

    assert response.status_code == 302
    admin_user.refresh_from_db()
    assert admin_user.check_password(NEW)


@pytest.mark.django_db
def test_counter_resets_after_a_successful_change(client, admin_user):
    client.force_login(admin_user)
    for _ in range(PASSWORD_CHANGE_MAX_ATTEMPTS - 1):
        post_change(client, old='no-es-esta')

    post_change(client)
    admin_user.refresh_from_db()
    assert admin_user.check_password(NEW)

    # Un fallo más no debe bloquear: el contador arrancó de cero.
    response = post_change(client, old='no-es-esta', new1='Otro-Secreto77')
    assert response.context['locked_minutes'] == 0


# ---------------------------------------------------------------------------
# htmx
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_htmx_success_returns_only_the_fragment(client, admin_user):
    client.force_login(admin_user)
    response = post_change(client, htmx=True)
    html = response.content.decode()

    assert response.status_code == 200
    assert '<html' not in html
    assert 'Contraseña actualizada correctamente' in html
    # Los campos enviados no vuelven al navegador.
    assert NEW not in html
    assert response['Cache-Control'] == 'private, no-store, max-age=0'

    admin_user.refresh_from_db()
    assert admin_user.check_password(NEW)


@pytest.mark.django_db
def test_htmx_error_returns_only_the_fragment(client, admin_user):
    client.force_login(admin_user)
    response = post_change(client, old='no-es-esta', htmx=True)
    html = response.content.decode()

    assert response.status_code == 200
    assert '<html' not in html
    assert 'La contraseña actual no es correcta' in html


@pytest.mark.django_db
def test_get_without_htmx_redirects_to_the_page(client, admin_user):
    """La tarjeta suelta no es una página; sin htmx se va a la que la contiene."""
    client.force_login(admin_user)
    response = client.get(reverse('core:password-change'))

    assert response.status_code == 302
    assert response['Location'] == reverse('core:account')


@pytest.mark.django_db
def test_get_with_htmx_returns_the_fragment(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('core:password-change'), HTTP_HX_REQUEST='true')
    html = response.content.decode()

    assert response.status_code == 200
    assert '<html' not in html
    assert 'id="password-card"' in html


@pytest.mark.django_db
def test_history_restore_is_not_treated_as_htmx(client, admin_user):
    """Volver atrás sin caché no puede dejar la página reducida al fragmento."""
    client.force_login(admin_user)
    response = client.get(
        reverse('core:password-change'),
        HTTP_HX_REQUEST='true',
        HTTP_HX_HISTORY_RESTORE_REQUEST='true',
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('core:account')


# ---------------------------------------------------------------------------
# Dónde se sirve la tarjeta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_card_is_rendered_on_the_account_tab(client, admin_user):
    """Su sitio es la pestaña «Cuenta», junto a los datos de acceso.

    Estuvo repetida en «Mi perfil» mientras fueron dos páginas; ahora esa ficha
    es otra pestaña de esta misma pantalla y la tarjeta no viaja con ella.
    """
    client.force_login(admin_user)
    html = client.get(reverse('core:account')).content.decode()

    assert 'id="password-card"' in html
    assert f'action="{CHANGE_URL}"' in html
