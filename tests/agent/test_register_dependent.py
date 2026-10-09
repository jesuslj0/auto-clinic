"""«Quiero pedir cita para mi padre»: el agente da de alta al familiar y, si hace falta, al contacto."""
import pytest

from patients.models import Guardian, Patient, PatientGuardian
from patients.services import MAX_DEPENDENTS_PER_GUARDIAN, add_guardian, create_patient

PHONE = '+34600111222'
URL = '/api/agent/sessions/register-dependent/'


@pytest.fixture
def agent(api_client, clinic_a):
    api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return api_client


def _body(**extra):
    return {'phone': '600 111 222', 'first_name': 'Luis', 'last_name': 'Pérez',
            'relationship': 'child', **extra}


@pytest.mark.django_db
def test_unknown_number_needs_requester_name(agent, clinic_a):
    response = agent.post(URL, _body(), format='json')
    assert response.status_code == 400 and response.json()['code'] == 'requester_name_required'
    assert not Patient.objects.filter(clinic=clinic_a).exists()
    assert not Guardian.objects.filter(clinic=clinic_a).exists()


@pytest.mark.django_db
def test_creates_guardian_dependent_and_link_then_can_book(agent, clinic_a):
    response = agent.post(
        URL, _body(requester_first_name='Juan', requester_last_name='Pérez'), format='json'
    )
    assert response.status_code == 201
    patient = Patient.objects.get(pk=response.json()['patient_id'])
    assert patient.phone == '' and patient.clinic == clinic_a
    guardian = Guardian.objects.get(clinic=clinic_a, phone=PHONE)
    assert guardian.first_name == 'Juan' and guardian.patient is None
    assert PatientGuardian.objects.get(patient=patient, guardian=guardian).relationship == 'child'

    ctx = agent.get('/api/agent/sessions/booking-context/', {'phone': PHONE}).json()
    assert ctx['ask_for_whom'] is True and [c['id'] for c in ctx['candidates']] == [patient.pk]
    ok = agent.get('/api/patients/', {'phone': PHONE, 'patient_id': patient.pk}).json()
    assert [p['id'] for p in ok['results']] == [patient.pk]


@pytest.mark.django_db
def test_requester_with_own_file_needs_no_name_and_is_linked_to_it(agent, clinic_a):
    me = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    response = agent.post(URL, _body(), format='json')
    assert response.status_code == 201
    guardian = Guardian.objects.get(clinic=clinic_a, phone=PHONE)
    assert guardian.patient == me
    ctx = agent.get('/api/agent/sessions/booking-context/', {'phone': PHONE}).json()
    assert {c['id'] for c in ctx['candidates']} == {me.pk, response.json()['patient_id']}


@pytest.mark.django_db
def test_is_idempotent_and_does_not_overwrite_guardian(agent, clinic_a):
    first = agent.post(URL, _body(requester_first_name='Juan'), format='json').json()
    again = agent.post(
        URL, _body(first_name='luis', requester_first_name='Otro'), format='json'
    )
    assert again.status_code == 200 and again.json()['patient_id'] == first['patient_id']
    assert again.json()['created'] is False
    assert Patient.objects.filter(clinic=clinic_a).count() == 1
    assert Guardian.objects.get(clinic=clinic_a).first_name == 'Juan'


@pytest.mark.django_db
def test_second_dependent_reuses_existing_guardian(agent, clinic_a):
    mother = Patient.objects.create(clinic=clinic_a, first_name='Rosa', last_name='Pérez')
    add_guardian(mother, first_name='Juan', phone=PHONE, relationship='child')
    response = agent.post(URL, _body(), format='json')
    assert response.status_code == 201
    guardian = Guardian.objects.get(clinic=clinic_a, phone=PHONE)
    assert guardian.links.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize('bad', [
    {'relationship': 'abuelo'}, {'relationship': ''}, {'last_name': ''}, {'phone': 'abc'},
])
def test_validation_errors_create_nothing(agent, clinic_a, bad):
    response = agent.post(URL, _body(requester_first_name='Juan', **bad), format='json')
    assert response.status_code == 400
    assert not Patient.objects.filter(clinic=clinic_a).exists()


@pytest.mark.django_db
def test_cap_on_dependents(agent, clinic_a):
    for i in range(MAX_DEPENDENTS_PER_GUARDIAN):
        r = agent.post(URL, _body(first_name=f'P{i}', requester_first_name='Juan'), format='json')
        assert r.status_code == 201
    r = agent.post(URL, _body(first_name='Extra'), format='json')
    assert r.status_code == 400 and r.json()['code'] == 'too_many_dependents'


@pytest.mark.django_db
def test_requires_agent_key_and_stays_in_its_clinic(api_client, agent, clinic_a, clinic_b):
    agent.post(URL, _body(requester_first_name='Juan'), format='json')
    assert Guardian.objects.filter(clinic=clinic_b).count() == 0
    api_client.credentials()
    assert api_client.post(URL, _body(), format='json').status_code in (401, 403)
