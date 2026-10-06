"""Plazos de reserva en el formulario y la ficha de la clínica."""
import pytest
from django.urls import reverse

from core.templatetags.core_extras import minutes_human

URL_EDIT = 'core:clinic-edit'


def _payload(clinic, **overrides):
    data = {
        'name': clinic.name, 'timezone': clinic.timezone, 'phone': '', 'email': '', 'website': '',
        'address': '', 'city': '', 'province': '', 'postal_code': '', 'description': '',
        'api_type': '', 'api_url': '',
        'hold_ttl_minutes': clinic.hold_ttl_minutes,
        'min_booking_notice_minutes': clinic.min_booking_notice_minutes,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestPlazosDeReserva:
    def test_se_guardan_desde_el_formulario(self, client, admin_user, clinic_a):
        client.force_login(admin_user)

        response = client.post(reverse(URL_EDIT), _payload(clinic_a, hold_ttl_minutes=90, min_booking_notice_minutes=0))

        assert response.status_code == 302
        clinic_a.refresh_from_db()
        assert clinic_a.hold_ttl_minutes == 90 and clinic_a.min_booking_notice_minutes == 0

    @pytest.mark.parametrize('field,value', [
        ('hold_ttl_minutes', -1), ('hold_ttl_minutes', 10081),
        ('min_booking_notice_minutes', -5), ('min_booking_notice_minutes', 43201),
    ])
    def test_valores_fuera_de_rango_se_rechazan(self, client, admin_user, clinic_a, field, value):
        client.force_login(admin_user)
        before = getattr(clinic_a, field)

        response = client.post(reverse(URL_EDIT), _payload(clinic_a, **{field: value}))

        assert response.status_code == 200
        clinic_a.refresh_from_db()
        assert getattr(clinic_a, field) == before

    def test_la_ficha_los_muestra_en_claro(self, client, admin_user, clinic_a):
        clinic_a.hold_ttl_minutes = 1440
        clinic_a.min_booking_notice_minutes = 0
        clinic_a.save()
        client.force_login(admin_user)

        html = client.get(reverse('core:clinic-info')).content.decode()

        assert '1 día' in html and 'Sin mínimo' in html


def test_minutes_human():
    assert minutes_human(45) == '45 min'
    assert minutes_human(90) == '1 h 30 min'
    assert minutes_human(1440) == '1 día'
    assert minutes_human(2 * 1440 + 60) == '2 días 1 h'
    assert minutes_human(0) == '0 min'


@pytest.mark.django_db
class TestSoloAdministracionEditaLaClinica:
    def test_el_staff_no_puede_abrir_ni_guardar(self, client, staff_user, clinic_a):
        client.force_login(staff_user)
        before = clinic_a.hold_ttl_minutes

        assert client.get(reverse(URL_EDIT)).status_code == 403
        assert client.post(reverse(URL_EDIT), _payload(clinic_a, hold_ttl_minutes=5)).status_code == 403

        clinic_a.refresh_from_db()
        assert clinic_a.hold_ttl_minutes == before

    def test_el_staff_ve_la_ficha_sin_el_boton_de_editar(self, client, staff_user, admin_user):
        client.force_login(staff_user)
        assert reverse(URL_EDIT) not in client.get(reverse('core:clinic-info')).content.decode()

        client.force_login(admin_user)
        assert reverse(URL_EDIT) in client.get(reverse('core:clinic-info')).content.decode()
