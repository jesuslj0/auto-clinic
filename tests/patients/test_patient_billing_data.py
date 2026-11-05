"""NIF y dirección (con código postal y ciudad) del paciente: datos para facturar.

Opcionales, validados cuando se escriben, auditados sin guardar su valor y fuera
de dos sitios a propósito: la ficha del paciente y la API del agente.
"""
import json

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from audit.models import ChangeLog
from patients.models import Patient
from patients.tax_id import normalize_tax_id, validate_tax_id


# ---------------------------------------------------------------------------
# El validador
# ---------------------------------------------------------------------------

class TestNormalize:
    @pytest.mark.parametrize('raw, expected', [
        ('12345678z', '12345678Z'),
        ('12.345.678-Z', '12345678Z'),
        (' x 1234567 l ', 'X1234567L'),
        ('', ''),
        (None, ''),
    ])
    def test_normalizes(self, raw, expected):
        assert normalize_tax_id(raw) == expected


class TestValidate:
    @pytest.mark.parametrize('value', [
        '',            # opcional
        '12345678Z',   # DNI con su letra
        '00000000T',
        'X1234567L',   # NIE
        'Y1234567X',
        'Z1234567R',
        'AB123456',    # pasaporte
        'K1234567L',   # NIF especial: sin regla común, se acepta
    ])
    def test_accepts(self, value):
        validate_tax_id(value)

    @pytest.mark.parametrize('value', [
        '12345678A',   # letra de control equivocada
        'X1234567A',   # NIE con letra equivocada
        'AB1',         # demasiado corto
        'AB12#456',    # símbolos
        'A' * 21,      # demasiado largo
    ])
    def test_rejects(self, value):
        with pytest.raises(ValidationError):
            validate_tax_id(value)

    def test_dni_letter_is_the_modulo_23_one(self):
        validate_tax_id('12345678Z')
        with pytest.raises(ValidationError) as caught:
            validate_tax_id('12345678X')
        assert caught.value.code == 'invalid_dni'


# ---------------------------------------------------------------------------
# Alta y edición desde el panel
# ---------------------------------------------------------------------------

def _data(**overrides):
    data = {
        'first_name': 'Ana', 'last_name': 'García', 'email': '', 'phone': '',
        'date_of_birth': '', 'tax_id': '', 'address': '', 'postal_code': '', 'city': '',
        'notes': '',
    }
    data.update(overrides)
    return data


@pytest.fixture
def staff_session(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.mark.django_db
class TestCreateForm:
    def test_the_form_asks_for_them(self, staff_session):
        html = staff_session.get(reverse('patients:create')).content.decode()

        for name in ('tax_id', 'address', 'postal_code', 'city'):
            assert f'name="{name}"' in html

    def test_they_are_saved_and_the_nif_is_normalized(self, staff_session):
        response = staff_session.post(reverse('patients:create'), _data(
            tax_id='12.345.678-z', address='Calle Mayor 1, 2º B',
            postal_code='28001', city='Madrid',
        ))

        assert response.status_code == 302
        patient = Patient.objects.get(first_name='Ana')
        assert patient.tax_id == '12345678Z'
        assert patient.address == 'Calle Mayor 1, 2º B'
        assert (patient.postal_code, patient.city) == ('28001', 'Madrid')

    def test_they_are_optional(self, staff_session):
        response = staff_session.post(reverse('patients:create'), _data())

        assert response.status_code == 302
        patient = Patient.objects.get(first_name='Ana')
        assert patient.tax_id == '' and patient.address == ''
        assert patient.postal_code == '' and patient.city == ''

    def test_a_wrong_dni_letter_sends_the_form_back(self, staff_session):
        response = staff_session.post(reverse('patients:create'), _data(tax_id='12345678A'))

        assert response.status_code == 200
        assert 'letra del DNI' in response.content.decode()
        assert not Patient.objects.filter(first_name='Ana').exists()

    def test_the_review_dialog_lists_them(self, staff_session):
        html = staff_session.get(reverse('patients:create')).content.decode()

        assert "['NIF / NIE / pasaporte', v('tax_id')]" in html
        assert "['Dirección', v('address')]" in html
        assert "['Código postal', v('postal_code')]" in html
        assert "['Ciudad', v('city')]" in html


@pytest.mark.django_db
class TestEditForm:
    def test_the_form_shows_the_stored_values(self, staff_session, patient_a):
        patient_a.tax_id = '12345678Z'
        patient_a.address = 'Calle Mayor 1'
        patient_a.postal_code = '28001'
        patient_a.city = 'Madrid'
        patient_a.save()

        html = staff_session.get(reverse('patients:edit', args=[patient_a.id])).content.decode()

        assert 'value="12345678Z"' in html
        assert 'value="Calle Mayor 1"' in html
        assert 'value="28001"' in html
        assert 'value="Madrid"' in html

    def test_they_can_be_edited(self, staff_session, patient_a):
        response = staff_session.post(reverse('patients:edit', args=[patient_a.id]), _data(
            first_name='John', last_name='Doe', phone='555-0001',
            tax_id='x1234567l', address='Plaza 2', postal_code='08001', city='Barcelona',
        ))

        assert response.status_code == 302
        patient_a.refresh_from_db()
        assert patient_a.tax_id == 'X1234567L'
        assert patient_a.address == 'Plaza 2'
        assert (patient_a.postal_code, patient_a.city) == ('08001', 'Barcelona')

    def test_they_can_be_cleared(self, staff_session, patient_a):
        patient_a.tax_id = '12345678Z'
        patient_a.address = 'Calle Mayor 1'
        patient_a.postal_code = '28001'
        patient_a.city = 'Madrid'
        patient_a.save()

        staff_session.post(reverse('patients:edit', args=[patient_a.id]), _data(
            first_name='John', last_name='Doe', phone='555-0001',
        ))

        patient_a.refresh_from_db()
        assert patient_a.tax_id == '' and patient_a.address == ''
        assert patient_a.postal_code == '' and patient_a.city == ''

    def test_an_invalid_nif_is_kept_in_the_redisplayed_form(self, staff_session, patient_a):
        response = staff_session.post(reverse('patients:edit', args=[patient_a.id]), _data(
            first_name='John', last_name='Doe', phone='555-0001', tax_id='12345678A',
        ))

        assert response.status_code == 200
        html = response.content.decode()
        assert 'value="12345678A"' in html
        assert 'letra del DNI' in html
        patient_a.refresh_from_db()
        assert patient_a.tax_id == ''


# ---------------------------------------------------------------------------
# Dónde NO salen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWhereTheyDoNotShow:
    def test_the_patient_file_does_not_show_them(self, staff_session, patient_a):
        patient_a.tax_id = '12345678Z'
        patient_a.address = 'Calle Secreta 99'
        patient_a.postal_code = '99999'
        patient_a.city = 'Ciudad Secreta'
        patient_a.save()

        html = staff_session.get(reverse('patients:detail', args=[patient_a.id])).content.decode()

        assert '12345678Z' not in html
        assert 'Calle Secreta 99' not in html
        assert '99999' not in html and 'Ciudad Secreta' not in html

    def test_the_directory_does_not_show_them(self, staff_session, patient_a):
        patient_a.tax_id = '12345678Z'
        patient_a.address = 'Calle Secreta 99'
        patient_a.postal_code = '99999'
        patient_a.city = 'Ciudad Secreta'
        patient_a.save()

        html = staff_session.get(reverse('patients:list')).content.decode()

        assert '12345678Z' not in html
        assert 'Calle Secreta 99' not in html
        assert '99999' not in html and 'Ciudad Secreta' not in html

    def test_the_agent_api_does_not_return_them(self, api_client, clinic_a, patient_a):
        patient_a.tax_id = '12345678Z'
        patient_a.address = 'Calle Secreta 99'
        patient_a.postal_code = '99999'
        patient_a.city = 'Ciudad Secreta'
        patient_a.save()
        api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')

        body = api_client.get(f'/api/patients/{patient_a.pk}/').json()

        for name in ('tax_id', 'address', 'postal_code', 'city'):
            assert name not in body
        assert '12345678Z' not in json.dumps(body)

    def test_the_agent_api_cannot_write_them(self, api_client, clinic_a):
        api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')

        response = api_client.post('/api/patients/', {
            'first_name': 'Eva', 'last_name': 'Ruiz', 'phone': '+34600111222',
            'email': 'eva@example.com', 'clinic': clinic_a.pk,
            'tax_id': '12345678Z', 'address': 'Calle Secreta 99',
            'postal_code': '99999', 'city': 'Ciudad Secreta',
        }, format='json')

        assert response.status_code == 201
        patient = Patient.objects.get(first_name='Eva')
        assert patient.tax_id == '' and patient.address == ''
        assert patient.postal_code == '' and patient.city == ''

    def test_the_audit_trail_records_the_change_but_not_the_value(self, staff_session, patient_a):
        staff_session.post(reverse('patients:edit', args=[patient_a.id]), _data(
            first_name='John', last_name='Doe', phone='555-0001',
            tax_id='12345678Z', address='Calle Secreta 99',
            postal_code='99999', city='Ciudad Secreta',
        ))

        logs = ChangeLog.objects.filter(
            model_label=Patient._meta.label, object_id=str(patient_a.pk),
        )
        blob = json.dumps([log.changes for log in logs], ensure_ascii=False)
        assert '12345678Z' not in blob
        assert 'Calle Secreta 99' not in blob
        assert '99999' not in blob and 'Ciudad Secreta' not in blob
        last = logs.order_by('timestamp').last()
        assert last.changes['tax_id'] == {'changed': True}
        assert last.changes['address'] == {'changed': True}
        assert last.changes['postal_code'] == {'changed': True}
        assert last.changes['city'] == {'changed': True}
