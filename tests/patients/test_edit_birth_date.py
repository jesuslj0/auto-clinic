"""La fecha de nacimiento llega rellena al formulario de edición del paciente."""
from datetime import date

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestEditBirthDate:
    def test_prefilled_in_iso_for_date_input(self, client, staff_user, patient_a):
        patient_a.date_of_birth = date(1980, 3, 7)
        patient_a.save()
        client.force_login(staff_user)

        html = client.get(reverse('patients:edit', args=[patient_a.id])).content.decode()

        assert 'value="1980-03-07"' in html

    def test_empty_when_unknown(self, client, staff_user, patient_a):
        client.force_login(staff_user)
        html = client.get(reverse('patients:edit', args=[patient_a.id])).content.decode()
        assert 'name="date_of_birth"' in html
        assert 'value="None"' not in html

    def test_kept_when_form_redisplayed_with_errors(self, client, staff_user, patient_a):
        client.force_login(staff_user)
        response = client.post(reverse('patients:edit', args=[patient_a.id]), {
            'first_name': '',  # inválido: obliga a volver a pintar el formulario
            'last_name': 'Doe',
            'phone': '600111222',
            'date_of_birth': '1975-12-31',
        })
        assert response.status_code == 200
        assert 'value="1975-12-31"' in response.content.decode()
