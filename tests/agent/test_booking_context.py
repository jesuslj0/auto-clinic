"""«¿Para quién?»: el agente solo atiende a los pacientes que su número puede gestionar."""
from datetime import timedelta

import pytest
from django.utils import timezone

from patients.models import Patient
from patients.services import add_guardian, create_patient

PHONE = '+34600111222'


@pytest.fixture
def agent(api_client, clinic_a):
    api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return api_client


def _patient(clinic, first, phone=''):
    return Patient.objects.create(clinic=clinic, first_name=first, last_name='Pérez', phone=phone)


@pytest.fixture
def family(clinic_a):
    mother, father, stranger = (_patient(clinic_a, n) for n in ('Rosa', 'Luis', 'Ajena'))
    add_guardian(mother, first_name='Juan', phone=PHONE, relationship='child')
    add_guardian(father, first_name='Juan', phone=PHONE, relationship='child')
    return mother, father, stranger


@pytest.mark.django_db
def test_guardian_number_must_ask_for_whom(agent, family):
    mother, father, _ = family
    data = agent.get('/api/agent/sessions/booking-context/', {'phone': '600 111 222'}).json()
    assert data['ask_for_whom'] is True
    assert {c['id'] for c in data['candidates']} == {mother.pk, father.pk}
    assert data['guardian']['first_name'] == 'Juan' and data['guardian']['has_own_file'] is False


@pytest.mark.django_db
def test_plain_number_does_not_ask(agent, clinic_a):
    _patient(clinic_a, 'Solo', '+34600999000')
    data = agent.get('/api/agent/sessions/booking-context/', {'phone': '+34600999000'}).json()
    assert data == {'ask_for_whom': False, 'guardian': None, 'candidates': []}


@pytest.mark.django_db
def test_guardian_who_is_also_a_patient_gets_self_candidate(agent, clinic_a, family):
    son = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    data = agent.get('/api/agent/sessions/booking-context/', {'phone': PHONE}).json()
    assert data['guardian']['has_own_file'] is True
    assert data['candidates'][0]['id'] == son.pk and data['candidates'][0]['is_self'] is True


@pytest.mark.django_db
def test_patients_filter_returns_only_allowed_patient(agent, family):
    mother, _, stranger = family
    url = '/api/patients/'
    ok = agent.get(url, {'phone': PHONE, 'patient_id': mother.pk}).json()
    assert [p['id'] for p in ok['results']] == [mother.pk]
    assert agent.get(url, {'phone': PHONE, 'patient_id': stranger.pk}).json()['results'] == []
    assert agent.get(url, {'patient_id': mother.pk}).json()['results'] == []


@pytest.mark.django_db
def test_agent_cannot_book_for_someone_else(agent, family, clinic_a, service_a, professional_a):
    mother, _, stranger = family
    when = (timezone.now() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)

    def book(patient, requester):
        return agent.post('/api/appointments/', {
            'clinic': clinic_a.pk, 'patient': patient.pk, 'service': service_a.pk,
            'professional': professional_a.pk, 'scheduled_at': when.isoformat(),
            'status': 'pending', **({'requester_phone': requester} if requester else {}),
        }, format='json')

    assert book(stranger, PHONE).status_code == 400
    assert book(mother, '').status_code == 400  # sin número no se puede comprobar
    assert 'patient' in book(stranger, PHONE).json()
    allowed = book(mother, PHONE)
    assert allowed.status_code == 201, allowed.json()
