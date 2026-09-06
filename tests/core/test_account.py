"""«Mi cuenta»: una pantalla con pestañas para lo que uno cambia de sí mismo.

Antes eran dos vistas («Mi cuenta» en core y «Mi perfil» en appointments) que
compartían la tarjeta de contraseña. Lo que se prueba aquí es el reparto que las
sustituyó: qué pestañas ve cada cual, que cada una edita lo suyo, y que la ficha
y el horario AJENOS siguen siendo cosa de administración.

El cambio de contraseña vive en `test_password_change.py`; aquí solo se
comprueba que su tarjeta sigue estando donde debe.
"""
from datetime import time
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from appointments.models import Professional, ProfessionalSchedule, ProfessionalTimeOff
from core.models import User
from services.models import Service

ACCOUNT_URL = '/cuenta/'
PROFILE_URL = '/cuenta/perfil/'
SCHEDULE_URL = '/cuenta/horario/'


def _tiny_png():
    # PNG 1x1 mínimo válido.
    data = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
        b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    return SimpleUploadedFile('avatar.png', data, content_type='image/png')


def _schedule_post(**overrides):
    """Management forms de los dos formsets, sin ninguna fila."""
    data = {
        'schedules-TOTAL_FORMS': '0',
        'schedules-INITIAL_FORMS': '0',
        'schedules-MIN_NUM_FORMS': '0',
        'schedules-MAX_NUM_FORMS': '1000',
        'timeoff-TOTAL_FORMS': '0',
        'timeoff-INITIAL_FORMS': '0',
        'timeoff-MIN_NUM_FORMS': '0',
        'timeoff-MAX_NUM_FORMS': '1000',
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Las URLs y las pestañas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'url_name,expected',
    [
        ('core:account', ACCOUNT_URL),
        ('core:account-profile', PROFILE_URL),
        ('core:account-schedule', SCHEDULE_URL),
    ],
)
def test_urls(url_name, expected):
    """Cada pestaña tiene URL propia: se comparte, se marca y se recarga."""
    assert reverse(url_name) == expected


@pytest.mark.django_db
@pytest.mark.parametrize('url', [ACCOUNT_URL, PROFILE_URL, SCHEDULE_URL])
def test_requires_login(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert '/login/' in response['Location']


@pytest.mark.django_db
def test_the_three_tabs_render_for_a_professional(client, staff_user):
    client.force_login(staff_user)
    for url in (ACCOUNT_URL, PROFILE_URL, SCHEDULE_URL):
        response = client.get(url)
        assert response.status_code == 200, url
        assert [tab['key'] for tab in response.context['account_tabs']] == [
            'cuenta', 'perfil', 'horario',
        ], url


@pytest.mark.django_db
def test_a_user_without_a_professional_only_sees_the_first_tab(client, superuser):
    """Un superusuario de plataforma no tiene ficha, pero sí contraseña."""
    assert getattr(superuser, 'professional_profile', None) is None

    client.force_login(superuser)
    response = client.get(ACCOUNT_URL)

    assert response.status_code == 200
    assert [tab['key'] for tab in response.context['account_tabs']] == ['cuenta']
    assert 'id="password-card"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize('url', [PROFILE_URL, SCHEDULE_URL])
def test_professional_tabs_redirect_when_there_is_no_profile(client, superuser, url):
    client.force_login(superuser)
    response = client.get(url)

    assert response.status_code == 302
    assert response['Location'] == ACCOUNT_URL


@pytest.mark.django_db
def test_htmx_returns_only_the_tab_region(client, staff_user):
    client.force_login(staff_user)
    response = client.get(SCHEDULE_URL, HTTP_HX_REQUEST='true')
    html = response.content.decode()

    assert response.status_code == 200
    assert '<html' not in html
    assert 'id="account-tab-panel"' in html


@pytest.mark.django_db
def test_history_restore_is_not_treated_as_htmx(client, staff_user):
    """Volver atrás sin caché no puede dejar la página reducida al fragmento."""
    client.force_login(staff_user)
    response = client.get(
        SCHEDULE_URL, HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true',
    )

    assert '<html' in response.content.decode()


@pytest.mark.django_db
def test_the_old_profile_url_redirects_to_the_tab(client, staff_user):
    """La ruta de «Mi perfil» pudo quedarse en un marcador."""
    client.force_login(staff_user)
    response = client.get(reverse('appointments:profile'))

    assert response.status_code == 302
    assert response['Location'] == PROFILE_URL


# ---------------------------------------------------------------------------
# Pestaña «Datos profesionales»
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_profile_updates_name_type_and_services(client, staff_user, service_a):
    client.force_login(staff_user)
    response = client.post(PROFILE_URL, {
        'first_name': 'Lucía',
        'last_name': 'Martín',
        'professional_type': Professional.ProfessionalType.DENTISTA,
        'services': [service_a.pk],
        'accepts_online_booking': 'on',
    })

    assert response.status_code == 302
    staff_user.refresh_from_db()
    professional = staff_user.professional_profile
    assert (staff_user.first_name, staff_user.last_name) == ('Lucía', 'Martín')
    assert professional.professional_type == Professional.ProfessionalType.DENTISTA
    assert service_a in professional.services.all()


@pytest.mark.django_db
def test_profile_uploads_photo(client, staff_user, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    client.force_login(staff_user)
    response = client.post(PROFILE_URL, {
        'first_name': 'Lucía',
        'last_name': 'Martín',
        'professional_type': Professional.ProfessionalType.MEDICO,
        'photo': _tiny_png(),
    })

    assert response.status_code == 302
    staff_user.professional_profile.refresh_from_db()
    assert staff_user.professional_profile.photo.name


@pytest.mark.django_db
def test_profile_toggles_online_booking(client, staff_user):
    """El único ajuste de la pestaña que no es un dato: la vía pública.

    Apagarlo saca al profesional de la reserva pública y del agente, pero no de
    la clínica: el staff puede seguir dándole citas a mano (`es_elegible()` en
    `appointments/services.py` solo lo mira con `require_online_booking`).
    """
    professional = staff_user.professional_profile
    assert professional.accepts_online_booking is True
    client.force_login(staff_user)

    base = {
        'first_name': 'Lucía',
        'last_name': 'Martín',
        'professional_type': Professional.ProfessionalType.MEDICO,
    }

    # Un checkbox sin marcar no viaja en el POST: eso es apagarlo.
    client.post(PROFILE_URL, base)
    professional.refresh_from_db()
    assert professional.accepts_online_booking is False

    client.post(PROFILE_URL, {**base, 'accepts_online_booking': 'on'})
    professional.refresh_from_db()
    assert professional.accepts_online_booking is True


@pytest.mark.django_db
def test_is_active_is_read_only_and_cannot_be_posted(client, staff_user):
    """`is_active` lo decide la clínica: se enseña, no se edita.

    Apagárselo uno mismo sería vaciarse la agenda por cualquier vía, así que no
    está en el formulario y un POST que lo intente no llega a ninguna parte.
    """
    professional = staff_user.professional_profile
    client.force_login(staff_user)

    html = client.get(ACCOUNT_URL).content.decode()
    assert 'En activo' in html

    client.post(PROFILE_URL, {
        'first_name': 'Lucía',
        'last_name': 'Martín',
        'professional_type': Professional.ProfessionalType.MEDICO,
        'is_active': '',
    })

    professional.refresh_from_db()
    assert professional.is_active is True


@pytest.mark.django_db
def test_an_inactive_professional_is_told_so(client, staff_user):
    professional = staff_user.professional_profile
    professional.is_active = False
    professional.save(update_fields=['is_active'])
    client.force_login(staff_user)

    assert 'No activo' in client.get(ACCOUNT_URL).content.decode()


@pytest.mark.django_db
def test_a_user_without_a_profile_has_no_status_row(client, superuser):
    """El estado es del profesional: sin ficha, no hay nada que decir."""
    client.force_login(superuser)
    html = client.get(ACCOUNT_URL).content.decode()

    assert 'En activo' not in html
    assert 'No activo' not in html


@pytest.mark.django_db
def test_every_tab_declares_an_icon(client, staff_user):
    """La pestaña y el título de su tarjeta comparten trazo (`_account_icon`)."""
    client.force_login(staff_user)
    tabs = client.get(ACCOUNT_URL).context['account_tabs']

    assert [tab['icon'] for tab in tabs] == ['cuenta', 'perfil', 'horario']


@pytest.mark.django_db
def test_profile_services_are_limited_to_own_clinic(client, staff_user, service_a, service_b):
    client.force_login(staff_user)
    queryset = client.get(PROFILE_URL).context['form'].fields['services'].queryset

    assert service_a in queryset
    assert service_b not in queryset


@pytest.mark.django_db
def test_profile_edits_only_your_own_record(client, staff_user, admin_user, service_a):
    """No hay forma de apuntar esta pestaña a la ficha de otro: no toma id."""
    client.force_login(staff_user)
    client.post(PROFILE_URL, {
        'first_name': 'Solo',
        'last_name': 'Yo',
        'professional_type': Professional.ProfessionalType.PODOLOGO,
    })

    admin_user.refresh_from_db()
    assert admin_user.first_name == 'Admin'
    assert admin_user.professional_profile.professional_type != Professional.ProfessionalType.PODOLOGO


# ---------------------------------------------------------------------------
# Pestaña «Horario y ausencias»
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_staff_saves_their_own_schedule(client, staff_user):
    client.force_login(staff_user)
    response = client.post(SCHEDULE_URL, _schedule_post(**{
        'schedules-TOTAL_FORMS': '1',
        'schedules-0-day_of_week': '1',
        'schedules-0-start_time': '09:00',
        'schedules-0-end_time': '14:00',
        'schedules-0-is_active': 'on',
    }))

    assert response.status_code == 302
    tramo = ProfessionalSchedule.objects.get(professional=staff_user.professional_profile)
    # Hora local tal cual: un horario recurrente no tiene UTC.
    assert (tramo.day_of_week, tramo.start_time, tramo.end_time) == (1, time(9, 0), time(14, 0))


@pytest.mark.django_db
def test_staff_saves_their_own_time_off_in_clinic_local_time(client, staff_user, clinic_a):
    clinic_a.timezone = 'Europe/Madrid'
    clinic_a.save(update_fields=['timezone'])
    client.force_login(staff_user)

    response = client.post(SCHEDULE_URL, _schedule_post(**{
        'timeoff-TOTAL_FORMS': '1',
        'timeoff-0-starts_at': '2026-07-01T09:00',
        'timeoff-0-ends_at': '2026-07-15T20:00',
        'timeoff-0-reason': ProfessionalTimeOff.Reason.VACATION,
        'timeoff-0-note': '',
    }))

    assert response.status_code == 302
    ausencia = ProfessionalTimeOff.objects.get(professional=staff_user.professional_profile)
    # Tecleado en hora de la clínica (verano: +02:00), guardado en UTC.
    assert ausencia.starts_at.astimezone(ZoneInfo('UTC')).hour == 7


@pytest.mark.django_db
def test_an_invalid_row_saves_nothing(client, staff_user):
    """Un formset inválido no escribe nada: ni su fila ni las del otro formset."""
    client.force_login(staff_user)
    response = client.post(SCHEDULE_URL, _schedule_post(**{
        'schedules-TOTAL_FORMS': '1',
        'schedules-0-day_of_week': '1',
        'schedules-0-start_time': '15:00',
        'schedules-0-end_time': '09:00',  # fin antes que inicio
        'schedules-0-is_active': 'on',
        'timeoff-TOTAL_FORMS': '1',
        'timeoff-0-starts_at': '2026-07-01T09:00',
        'timeoff-0-ends_at': '2026-07-15T20:00',
        'timeoff-0-reason': ProfessionalTimeOff.Reason.VACATION,
        'timeoff-0-note': '',
    }))

    assert response.status_code == 200
    assert not ProfessionalSchedule.objects.filter(professional=staff_user.professional_profile).exists()
    assert not ProfessionalTimeOff.objects.filter(professional=staff_user.professional_profile).exists()


# ---------------------------------------------------------------------------
# La ficha ajena sigue siendo de administración
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('url_name', ['appointments:professionals-create'])
def test_staff_cannot_reach_professional_management(client, staff_user, url_name):
    client.force_login(staff_user)
    assert client.get(reverse(url_name)).status_code == 403


@pytest.mark.django_db
def test_staff_cannot_edit_another_professional(client, staff_user, professional_a):
    client.force_login(staff_user)
    url = reverse('appointments:professionals-edit', args=[professional_a.pk])

    assert client.get(url).status_code == 403
    assert client.post(url, {}).status_code == 403


@pytest.mark.django_db
def test_admin_still_manages_professionals(client, admin_user, professional_a):
    client.force_login(admin_user)
    url = reverse('appointments:professionals-edit', args=[professional_a.pk])

    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_the_professional_list_hides_management_from_staff(client, staff_user, admin_user):
    """Recepción sigue viendo quién atiende; un botón prohibido sería una trampa."""
    client.force_login(staff_user)
    response = client.get(reverse('appointments:professionals-list'))
    html = response.content.decode()

    assert response.status_code == 200
    assert response.context['can_manage'] is False
    assert 'professionals/crear/' not in html
    assert '/editar/' not in html


@pytest.mark.django_db
def test_the_professional_list_shows_management_to_admin(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse('appointments:professionals-list'))

    assert response.context['can_manage'] is True
    assert '/editar/' in response.content.decode()
