"""Citas sin ficha de paciente: primera visita desde el panel y reserva del agente.

Una cita puede existir antes que el paciente. Lleva su contacto (`patient_name`,
`patient_phone`, `contact_email`) y `patient` vacío; la ficha se abre después, y el
teléfono es lo que une ambas cosas. Se defiende aquí:

1. El panel crea la cita sin ficha, exigiendo nombre y teléfono, y sin crear
   ningún `Patient`.
2. Si el teléfono ya es de un paciente de la clínica, la cita se enlaza sola.
3. La API (agente) exige el contacto si no hay paciente, y lo devuelve en la
   respuesta.
4. Crear una ficha enlaza las citas huérfanas de ese teléfono — solo de su clínica.
5. «Crear ficha desde la cita» crea o vincula, sin duplicar.
6. Sin ficha no se puede completar la cita ni registrar procedimientos.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from appointments.services import create_appointment, create_patient_from_appointment
from patients.models import Patient
from patients.services import create_patient

PHONE = '+34600111222'


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def manana():
    return timezone.localdate() + timedelta(days=1)


def form_data(service, professional, date, **kwargs):
    data = {
        'no_record': 'true',
        'patient_name': 'Marta Gil Soto',
        'patient_phone': '600 111 222',
        'contact_email': 'marta@example.com',
        'service': service.pk,
        'professional': professional.pk,
        'date': date.isoformat(),
        'time': '10:00',
        'notes': '',
    }
    data.update(kwargs)
    return data


def make_orphan(clinic, service, professional, days=1, **kwargs):
    start = timezone.now() + timedelta(days=days)
    defaults = dict(patient_name='Marta Gil Soto', patient_phone=PHONE)
    defaults.update(kwargs)
    return create_appointment(
        clinic=clinic, scheduled_at=start, service=service, professional=professional,
        require_online_booking=False, source=Appointment.Source.STAFF, **defaults,
    )


@pytest.mark.django_db
class TestPanelCreate:
    def test_creates_without_patient_and_without_file(
        self, panel_client, service_a, professional_a, manana
    ):
        response = panel_client.post(
            reverse('appointments:create'), form_data(service_a, professional_a, manana)
        )
        assert response.status_code == 302
        appointment = Appointment.objects.get()
        assert appointment.patient_id is None
        assert appointment.patient_name == 'Marta Gil Soto'
        assert appointment.patient_phone == PHONE
        assert appointment.contact_email == 'marta@example.com'
        assert not Patient.objects.filter(first_name='Marta').exists()

    def test_requires_name_and_phone(self, panel_client, service_a, professional_a, manana):
        response = panel_client.post(
            reverse('appointments:create'),
            form_data(service_a, professional_a, manana, patient_name='', patient_phone=''),
        )
        assert response.status_code == 200
        form = response.context['form']
        assert 'patient_name' in form.errors and 'patient_phone' in form.errors
        assert not Appointment.objects.exists()

    def test_rejects_invalid_phone(self, panel_client, service_a, professional_a, manana):
        response = panel_client.post(
            reverse('appointments:create'),
            form_data(service_a, professional_a, manana, patient_phone='123'),
        )
        assert response.status_code == 200
        assert 'patient_phone' in response.context['form'].errors

    def test_with_file_still_needs_a_patient(self, panel_client, service_a, professional_a, manana):
        response = panel_client.post(
            reverse('appointments:create'),
            form_data(service_a, professional_a, manana, no_record='false'),
        )
        assert response.status_code == 200
        assert 'patient' in response.context['form'].errors

    def test_with_patient_ignores_contact_fields(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        data = form_data(
            service_a, professional_a, manana, no_record='false', patient=patient_a.pk
        )
        panel_client.post(reverse('appointments:create'), data)
        appointment = Appointment.objects.get()
        assert appointment.patient == patient_a
        assert appointment.patient_name == ''
        assert appointment.contact_email == ''

    def test_phone_of_existing_patient_links_automatically(
        self, panel_client, clinic_a, service_a, professional_a, manana
    ):
        patient = create_patient(clinic=clinic_a, phone=PHONE, first_name='Marta', last_name='Gil')
        panel_client.post(reverse('appointments:create'), form_data(service_a, professional_a, manana))
        assert Appointment.objects.get().patient == patient


@pytest.mark.django_db
class TestLinking:
    def test_new_patient_claims_orphans_of_same_phone_only(
        self, clinic_a, clinic_b, service_a, professional_a, patient_b
    ):
        mine = make_orphan(clinic_a, service_a, professional_a)
        other_phone = make_orphan(
            clinic_a, service_a, professional_a, days=3, patient_phone='+34699000111',
        )
        # Misma persona, pero en OTRA clínica: no se toca.
        other_clinic = Appointment.objects.create(
            clinic=clinic_b, scheduled_at=timezone.now() + timedelta(days=2),
            patient_name='Marta', patient_phone=PHONE,
        )

        patient = create_patient(clinic=clinic_a, phone=PHONE, first_name='Marta', last_name='Gil')

        mine.refresh_from_db()
        other_phone.refresh_from_db()
        other_clinic.refresh_from_db()
        assert mine.patient == patient
        assert other_phone.patient_id is None
        assert other_clinic.patient_id is None

    def test_create_patient_from_appointment_creates_and_links(
        self, clinic_a, service_a, professional_a
    ):
        appointment = make_orphan(clinic_a, service_a, professional_a, contact_email='m@example.com')
        patient, created = create_patient_from_appointment(appointment)
        appointment.refresh_from_db()
        assert created
        assert appointment.patient == patient
        assert (patient.first_name, patient.last_name) == ('Marta', 'Gil Soto')
        assert patient.phone == PHONE
        assert patient.email == 'm@example.com'

    def test_create_patient_from_appointment_does_not_duplicate(
        self, clinic_a, service_a, professional_a
    ):
        existing = Patient.objects.create(
            clinic=clinic_a, first_name='Marta', last_name='G', phone=PHONE
        )
        # El teléfono de la cita entró sin normalizar: el enlace tiene que casar igual.
        appointment = make_orphan(clinic_a, service_a, professional_a)
        Appointment.objects.filter(pk=appointment.pk).update(patient=None, patient_phone='600111222')
        appointment.refresh_from_db()
        patient, created = create_patient_from_appointment(appointment)
        assert not created
        assert patient == existing
        assert Patient.objects.filter(clinic=clinic_a, phone=PHONE).count() == 1

    def test_create_patient_from_appointment_needs_a_name(
        self, clinic_a, service_a, professional_a
    ):
        appointment = make_orphan(clinic_a, service_a, professional_a, patient_name='')
        with pytest.raises(ValueError):
            create_patient_from_appointment(appointment)


@pytest.mark.django_db
class TestManageScreen:
    def test_create_patient_action(self, panel_client, clinic_a, service_a, professional_a):
        appointment = make_orphan(clinic_a, service_a, professional_a)
        url = reverse('core:dashboard-appointment-action', args=[appointment.pk])
        response = panel_client.post(url, {'action': 'create_patient'})
        assert response.status_code == 302
        appointment.refresh_from_db()
        assert appointment.patient is not None
        assert appointment.patient.phone == PHONE

    def test_manage_page_offers_the_action_only_without_file(
        self, panel_client, clinic_a, service_a, professional_a, appointment_a
    ):
        orphan = make_orphan(clinic_a, service_a, professional_a)
        html = panel_client.get(
            reverse('core:dashboard-manage-appointment', args=[orphan.pk])
        ).content.decode()
        assert 'Crear ficha de paciente' in html
        html = panel_client.get(
            reverse('core:dashboard-manage-appointment', args=[appointment_a.pk])
        ).content.decode()
        assert 'Crear ficha de paciente' not in html

    def test_cannot_complete_without_file(self, panel_client, clinic_a, service_a, professional_a):
        appointment = make_orphan(clinic_a, service_a, professional_a)
        Appointment.objects.filter(pk=appointment.pk).update(status=Appointment.Status.CONFIRMED)
        url = reverse('core:dashboard-appointment-action', args=[appointment.pk])
        panel_client.post(url, {'action': 'complete'})
        appointment.refresh_from_db()
        assert appointment.status == Appointment.Status.CONFIRMED

    def test_cannot_register_procedure_without_file(
        self, panel_client, clinic_a, service_a, professional_a
    ):
        appointment = make_orphan(clinic_a, service_a, professional_a)
        url = reverse('appointments:procedure-create', args=[appointment.pk])
        assert panel_client.post(url, {}).status_code == 403

    def test_other_clinic_cannot_create_the_file(
        self, client, admin_user_b, clinic_a, service_a, professional_a
    ):
        appointment = make_orphan(clinic_a, service_a, professional_a)
        client.force_login(admin_user_b)
        url = reverse('core:dashboard-appointment-action', args=[appointment.pk])
        client.post(url, {'action': 'create_patient'})
        assert not Patient.objects.filter(phone=PHONE).exists()


@pytest.mark.django_db
class TestApi:
    def payload(self, clinic, service, professional, **kwargs):
        start = timezone.localtime() + timedelta(days=1)
        start = start.replace(hour=10, minute=0, second=0, microsecond=0)
        data = {
            'clinic': clinic.pk,
            'service': service.pk,
            'professional': professional.pk,
            'scheduled_at': start.isoformat(),
        }
        data.update(kwargs)
        return data

    def test_create_with_contact_only(self, admin_client, clinic_a, service_a, professional_a):
        response = admin_client.post(
            '/api/appointments/',
            self.payload(
                clinic_a, service_a, professional_a,
                patient_name='Marta Gil', patient_phone='600111222',
            ),
        )
        assert response.status_code == 201, response.data
        assert response.data['patient'] is None
        assert response.data['patient_name'] == 'Marta Gil'
        assert response.data['patient_phone'] == PHONE

    def test_create_without_patient_or_contact_is_rejected(
        self, admin_client, clinic_a, service_a, professional_a
    ):
        response = admin_client.post(
            '/api/appointments/', self.payload(clinic_a, service_a, professional_a)
        )
        assert response.status_code == 400
        assert 'patient_name' in response.data and 'patient_phone' in response.data

    def test_contact_of_known_patient_links_it(
        self, admin_client, clinic_a, service_a, professional_a
    ):
        patient = create_patient(clinic=clinic_a, phone=PHONE, first_name='Marta', last_name='Gil')
        response = admin_client.post(
            '/api/appointments/',
            self.payload(
                clinic_a, service_a, professional_a,
                patient_name='Marta', patient_phone=PHONE,
            ),
        )
        assert response.status_code == 201, response.data
        assert response.data['patient'] == patient.pk
        assert response.data['patient_name'] == 'Marta Gil'
