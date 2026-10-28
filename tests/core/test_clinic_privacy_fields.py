"""Campos de protección de datos de la clínica: política de privacidad y contacto de derechos."""
import pytest
from django.urls import reverse

from tests.core.test_clinic_booking_settings import URL_EDIT, _payload


@pytest.mark.django_db
class TestPrivacyFields:
    def test_se_guardan_desde_el_formulario(self, client, admin_user, clinic_a):
        client.force_login(admin_user)

        response = client.post(reverse(URL_EDIT), _payload(
            clinic_a,
            privacy_policy_url='https://clinica.example/privacidad',
            privacy_contact_email='dpd@clinica.example',
        ))

        assert response.status_code == 302
        clinic_a.refresh_from_db()
        assert clinic_a.privacy_policy_url == 'https://clinica.example/privacidad'
        assert clinic_a.privacy_contact_email == 'dpd@clinica.example'

    def test_son_opcionales(self, client, admin_user, clinic_a):
        client.force_login(admin_user)

        assert client.post(reverse(URL_EDIT), _payload(clinic_a)).status_code == 302

    @pytest.mark.parametrize('field,value', [
        ('privacy_policy_url', 'javascript:alert(1)'),
        ('privacy_policy_url', 'no es una url'),
        ('privacy_contact_email', 'sin-arroba'),
    ])
    def test_rechaza_valores_invalidos(self, client, admin_user, clinic_a, field, value):
        client.force_login(admin_user)

        response = client.post(reverse(URL_EDIT), _payload(clinic_a, **{field: value}))

        assert response.status_code == 200
        assert field in response.context['form'].errors

    def test_contacto_de_derechos_cae_al_correo_general_y_luego_al_telefono(self, clinic_a):
        clinic_a.privacy_contact_email, clinic_a.email, clinic_a.phone = 'dpd@x.es', 'info@x.es', '600000000'
        assert clinic_a.data_protection_contact == 'dpd@x.es'

        clinic_a.privacy_contact_email = ''
        assert clinic_a.data_protection_contact == 'info@x.es'

        clinic_a.email = ''
        assert clinic_a.data_protection_contact == '600000000'
