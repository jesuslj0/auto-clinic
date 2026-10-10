import pytest

from patients.services import create_patient

PHONE = '+34600111222'


@pytest.fixture
def agent(api_client, clinic_a):
    api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return api_client


@pytest.mark.django_db
def test_patient_list_serializes_existing_patient_and_phone_stays_required(agent, clinic_a):
    """Regresión: DRF 3.18 daba 500 al serializar un paciente (required + default en `phone`)."""
    create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    listed = agent.get('/api/patients/', {'phone': PHONE, 'patient_id': ''})
    assert listed.status_code == 200 and listed.json()['count'] == 1
    created = agent.post('/api/patients/', {'first_name': 'Ana', 'last_name': 'Ruiz'}, format='json')
    assert created.status_code == 400 and 'phone' in created.json()
