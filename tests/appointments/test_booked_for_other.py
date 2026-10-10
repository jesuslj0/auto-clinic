"""«Quiero cita para mi padre»: entra como cita sin ficha; la ficha la abre el admin."""
from datetime import timedelta

import pytest
from django.utils import timezone

from appointments.models import Appointment
from appointments.services import create_patient_from_appointment
from patients.models import Guardian, Patient, PatientGuardian
from patients.services import create_patient

PHONE = '+34600111222'


@pytest.fixture
def agent(api_client, clinic_a):
    api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return api_client


@pytest.fixture
def book(agent, clinic_a, service_a, professional_a):
    when = (timezone.now() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)

    def _book(**extra):
        body = {
            'clinic': clinic_a.pk, 'service': service_a.pk, 'professional': professional_a.pk,
            'scheduled_at': when.isoformat(), 'status': 'pending',
            'booked_for_other': True, 'patient_name': 'Luis Pérez', 'patient_phone': '600 111 222',
            'contact_relationship': 'child', **extra,
        }
        return agent.post('/api/appointments/', body, format='json')

    return _book


@pytest.mark.django_db
def test_known_requester_does_not_adopt_the_appointment(book, clinic_a):
    me = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    response = book()
    assert response.status_code == 201, response.json()
    appointment = Appointment.objects.get(pk=response.json()['id'])
    assert appointment.patient is None and appointment.booked_for_other
    assert appointment.patient_name == 'Luis Pérez' and appointment.patient_phone == PHONE
    assert appointment.contact_name == 'Juan Pérez'  # sale de su ficha
    assert not Patient.objects.filter(clinic=clinic_a, first_name='Luis').exists()
    assert me.appointments.count() == 0


@pytest.mark.django_db
def test_unknown_requester_must_give_a_name(book, clinic_a):
    response = book()
    assert response.status_code == 400 and 'contact_name' in response.json()
    ok = book(contact_name='Juan Pérez')
    assert ok.status_code == 201 and ok.json()['contact_name'] == 'Juan Pérez'
    assert not Patient.objects.filter(clinic=clinic_a).exists()
    assert not Guardian.objects.filter(clinic=clinic_a).exists()


@pytest.mark.django_db
def test_rejects_patient_and_bad_relationship(book, clinic_a):
    me = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    assert 'patient' in book(patient=me.pk).json()
    assert 'contact_relationship' in book(contact_relationship='abuelo').json()


@pytest.mark.django_db
def test_requester_getting_a_file_later_does_not_adopt_it(book, clinic_a):
    book(contact_name='Juan Pérez')
    create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    assert Appointment.objects.get(clinic=clinic_a).patient is None


@pytest.mark.django_db
def test_admin_creates_the_file_without_phone_and_requester_becomes_its_contact(book, clinic_a):
    me = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    appointment = Appointment.objects.get(pk=book().json()['id'])

    patient, created = create_patient_from_appointment(appointment)

    assert created and patient != me
    assert (patient.first_name, patient.last_name, patient.phone) == ('Luis', 'Pérez', '')
    appointment.refresh_from_db()
    assert appointment.patient == patient
    link = PatientGuardian.objects.get(patient=patient)
    assert link.relationship == 'child' and link.guardian.phone == PHONE
    assert link.guardian.patient == me  # y puede seguir reservando para sí mismo
    assert me.appointments.count() == 0


@pytest.mark.django_db
def test_normal_appointment_without_file_still_links_by_phone(agent, clinic_a, service_a, professional_a):
    me = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    when = (timezone.now() + timedelta(days=5)).replace(hour=11, minute=0, second=0, microsecond=0)
    response = agent.post('/api/appointments/', {
        'clinic': clinic_a.pk, 'service': service_a.pk, 'professional': professional_a.pk,
        'scheduled_at': when.isoformat(), 'status': 'pending',
        'patient_name': 'Juan Pérez', 'patient_phone': '600 111 222',
    }, format='json')
    assert response.status_code == 201, response.json()
    assert Appointment.objects.get(pk=response.json()['id']).patient == me
