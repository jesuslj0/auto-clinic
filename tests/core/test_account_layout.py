"""Distribución de «Mi cuenta»: qué va en cada pestaña y cómo se presenta.

- «Cuenta» enseña la foto junto a los datos de acceso; la contraseña ya no está.
- «Datos profesionales» se ve fija y un botón la pasa a edición.
- «Seguridad» pone la contraseña primero y la verificación en dos pasos debajo,
  con el estado en la misma línea que el título.
"""
import pytest
from django.urls import reverse

from appointments.models import Professional

ACCOUNT_URL = '/cuenta/'
PROFILE_URL = '/cuenta/perfil/'
SECURITY_URL = '/cuenta/seguridad/'


@pytest.fixture
def staff_session(client, staff_user):
    client.force_login(staff_user)
    return client


def _security_state(monkeypatch, **overrides):
    """Fija lo que la pestaña «Seguridad» cree del 2FA, sin tocar el middleware.

    Se parchea `security_tab_context` y no `core.two_factor`: el middleware que
    exige el segundo factor lee del mismo módulo, y falsear ahí que el usuario lo
    necesita le cerraría la sesión al propio test.
    """
    state = {
        'two_factor_active': False, 'two_factor_required': False,
        'recovery_codes_left': 0, 'recovery_codes_total': 10,
        **overrides,
    }
    monkeypatch.setattr('core.views.security_tab_context', lambda user: state)


def _html(client, url):
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


# ---------------------------------------------------------------------------
# Cuenta: la foto
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAccountTabShowsThePhoto:
    def test_the_photo_is_shown(self, staff_session, staff_user):
        professional = staff_user.professional_profile
        professional.photo = 'professional_photos/lucia.jpg'
        professional.save()

        html = _html(staff_session, ACCOUNT_URL)

        assert professional.photo.url in html
        assert 'alt="Foto de perfil"' in html

    def test_without_a_photo_the_initials_are_shown(self, staff_session):
        html = _html(staff_session, ACCOUNT_URL)

        assert 'alt="Foto de perfil"' not in html
        assert 'bg-brand-gradient' in html

    def test_a_professional_can_jump_to_change_it(self, staff_session):
        html = _html(staff_session, ACCOUNT_URL)

        assert 'Cambiar foto' in html
        assert f'href="{PROFILE_URL}"' in html

    def test_without_a_professional_profile_there_is_nothing_to_change(self, client, superuser):
        client.force_login(superuser)

        html = _html(client, ACCOUNT_URL)

        assert 'Cambiar foto' not in html
        assert 'bg-brand-gradient' in html  # iniciales

    def test_the_password_is_not_on_this_tab_any_more(self, staff_session):
        html = _html(staff_session, ACCOUNT_URL)

        assert 'id="password-card"' not in html
        assert 'Cambiar contraseña' not in html


# ---------------------------------------------------------------------------
# Datos profesionales: fijo, con botón para editar
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProfileIsReadOnlyUntilYouAskToEdit:
    def test_it_starts_in_read_mode_with_an_edit_button(self, staff_session):
        html = _html(staff_session, PROFILE_URL)

        assert 'x-data="{ editing: false }"' in html
        assert 'Editar datos' in html

    def test_the_form_is_hidden_until_editing(self, staff_session):
        html = _html(staff_session, PROFILE_URL)
        form_tag = html[html.index('<form method="post" action="/cuenta/perfil/"'):]
        form_tag = form_tag[:form_tag.index('>')]

        assert 'x-show="editing"' in form_tag
        assert 'x-cloak' in form_tag

    def test_the_saved_values_are_shown_as_text(self, staff_session, staff_user, service_a):
        professional = staff_user.professional_profile
        professional.title = Professional.Title.DRA
        professional.bio = 'Podóloga especializada en pie diabético.'
        professional.license_number = '28/1234'
        professional.license_body = 'Colegio de Madrid'
        professional.save()
        professional.services.add(service_a)

        html = _html(staff_session, PROFILE_URL)
        read_mode = html[:html.index('<form method="post" action="/cuenta/perfil/"')]

        assert 'Podóloga especializada en pie diabético.' in read_mode
        assert '28/1234 · Colegio de Madrid' in read_mode
        assert 'Dra.' in read_mode
        assert service_a.name in read_mode
        assert 'Aceptas reservas online' in read_mode

    def test_empty_fields_show_a_dash_not_blank(self, staff_session):
        html = _html(staff_session, PROFILE_URL)
        read_mode = html[:html.index('<form method="post" action="/cuenta/perfil/"')]

        assert 'Sin tratamiento' in read_mode
        assert 'Ninguno todavía.' in read_mode

    def test_online_booking_off_is_told(self, staff_session, staff_user):
        professional = staff_user.professional_profile
        professional.accepts_online_booking = False
        professional.save()

        read_mode = _html(staff_session, PROFILE_URL)

        assert 'Sin reservas online' in read_mode

    def test_a_cancel_button_is_offered_while_editing(self, staff_session):
        html = _html(staff_session, PROFILE_URL)

        assert '$root.reset()' in html
        assert 'editing = false' in html

    def test_a_rejected_save_reopens_the_form_in_edit_mode(self, staff_session):
        response = staff_session.post(reverse('core:account-profile'), {
            'first_name': 'Lucía', 'last_name': 'Martín',
            'professional_type': 'podologo', 'bio': 'x' * 501,
        })

        assert response.status_code == 200
        html = response.content.decode()
        assert 'x-data="{ editing: true }"' in html
        # Con un envío fallido, «Cancelar» recarga la pestaña limpia en vez de
        # dejar los valores inválidos como si estuvieran guardados.
        assert f'href="{PROFILE_URL}"' in html

    def test_saving_still_works(self, staff_session, staff_user):
        response = staff_session.post(reverse('core:account-profile'), {
            'first_name': 'Lucía', 'last_name': 'Martín',
            'professional_type': 'podologo', 'bio': 'Nueva presentación',
            'accepts_online_booking': 'on',
        })

        assert response.status_code == 302
        assert response['Location'] == PROFILE_URL
        staff_user.professional_profile.refresh_from_db()
        assert staff_user.professional_profile.bio == 'Nueva presentación'
        assert 'Nueva presentación' in _html(staff_session, PROFILE_URL)


# ---------------------------------------------------------------------------
# Seguridad: contraseña primero, verificación en dos pasos debajo
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSecurityTabOrderAndHeader:
    def test_the_password_comes_before_the_two_factor_card(self, staff_session):
        html = _html(staff_session, SECURITY_URL)

        assert html.index('id="password-card"') < html.index('Verificación en dos pasos')

    @pytest.mark.parametrize('active, badge', [(True, 'Activa'), (False, 'No activa')])
    def test_the_badge_sits_in_the_title_row_and_the_text_goes_below(
        self, staff_session, monkeypatch, active, badge
    ):
        _security_state(monkeypatch, two_factor_active=active)

        html = _html(staff_session, SECURITY_URL)
        card = html[html.index('<h2 class="text-lg font-semibold text-content">Verificación en dos pasos</h2>'):]
        title_row_end = card.index('</div>\n        <p')  # cierra la fila título + estado
        title_row = card[:title_row_end]

        assert badge in title_row                      # el estado, en la fila del título
        assert 'Además de tu contraseña' not in title_row
        assert card.index('Además de tu contraseña') > title_row_end   # la explicación, debajo

    def test_the_required_by_role_notice_carries_a_security_icon(
        self, staff_session, monkeypatch
    ):
        _security_state(monkeypatch, two_factor_required=True)

        html = _html(staff_session, SECURITY_URL)
        start = html.index('Tu rol exige la verificación en dos pasos')
        notice = html[html.rindex('<p class="flex', 0, start):start]

        assert '<svg' in notice
        assert 'M9 12.75L11.25 15 15 9.75' in notice   # el trazo del escudo

    def test_without_the_requirement_there_is_no_notice(self, staff_session, monkeypatch):
        _security_state(monkeypatch, two_factor_required=False)

        assert 'Tu rol exige' not in _html(staff_session, SECURITY_URL)

    def test_the_tab_works_without_a_professional_profile(self, client, superuser):
        client.force_login(superuser)

        html = _html(client, SECURITY_URL)

        assert 'id="password-card"' in html
        assert 'Verificación en dos pasos' in html

    def test_htmx_swaps_the_whole_region_with_both_cards(self, staff_session):
        response = staff_session.get(SECURITY_URL, HTTP_HX_REQUEST='true')
        html = response.content.decode()

        assert '<html' not in html
        assert 'id="password-card"' in html
        assert 'Verificación en dos pasos' in html
