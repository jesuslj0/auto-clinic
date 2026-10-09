"""Contactos responsables: un teléfono, un contacto; reservan solo para los suyos."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.services import get_or_create_session
from appointments.models import Appointment
from appointments.services import PatientArchived, create_appointment
from patients.models import Guardian, Patient, PatientGuardian
from patients.services import add_guardian, create_patient

PHONE = '+34600111222'


def _patient(clinic, first, phone=''):
    return Patient.objects.create(clinic=clinic, first_name=first, last_name='Pérez', phone=phone)


@pytest.mark.django_db
def test_son_manages_both_parents(clinic_a):
    mother, father = _patient(clinic_a, 'Rosa'), _patient(clinic_a, 'Luis')
    add_guardian(mother, first_name='Juan', phone='600 111 222', relationship='child')
    link, created = add_guardian(father, first_name='Otro', phone=PHONE, relationship='child')

    assert not created  # mismo teléfono = mismo contacto, no se pisan sus datos
    guardian = link.guardian
    assert guardian.first_name == 'Juan'
    assert set(guardian.bookable_patients()) == {mother, father}


@pytest.mark.django_db
def test_bookable_includes_self_when_guardian_is_patient_and_excludes_strangers_and_archived(clinic_a):
    mother, stranger = _patient(clinic_a, 'Rosa'), _patient(clinic_a, 'Ajena')
    son = _patient(clinic_a, 'Juan', PHONE)
    link, _ = add_guardian(mother, first_name='Juan', phone=PHONE, relationship='child')

    assert link.guardian.patient == son  # su teléfono es el de una ficha: se enlaza sola
    assert set(link.guardian.bookable_patients()) == {mother, son}
    assert stranger not in link.guardian.bookable_patients()

    mother.archive()
    assert set(link.guardian.bookable_patients()) == {son}


@pytest.mark.django_db
def test_patient_created_later_with_guardian_phone_is_linked(clinic_a):
    mother = _patient(clinic_a, 'Rosa')
    link, _ = add_guardian(mother, first_name='Juan', phone=PHONE, relationship='child')
    son = create_patient(clinic=clinic_a, phone=PHONE, first_name='Juan', last_name='Pérez')
    link.guardian.refresh_from_db()
    assert link.guardian.patient == son


@pytest.mark.django_db
def test_cannot_be_own_guardian_or_duplicate_or_cross_clinic(clinic_a, clinic_b):
    own = _patient(clinic_a, 'Rosa', PHONE)
    with pytest.raises(ValueError):
        add_guardian(own, first_name='Rosa', phone=PHONE, relationship='other')

    patient = _patient(clinic_a, 'Luis')
    add_guardian(patient, first_name='Juan', phone='+34600999888', relationship='child')
    with pytest.raises(ValueError):
        add_guardian(patient, first_name='Juan', phone='+34600999888', relationship='child')

    guardian = Guardian.objects.get(phone='+34600999888')
    with pytest.raises(Exception):
        PatientGuardian.objects.create(patient=_patient(clinic_b, 'X'), guardian=guardian)


@pytest.mark.django_db
def test_patients_without_phone_can_coexist(clinic_a):
    _patient(clinic_a, 'A')
    _patient(clinic_a, 'B')


@pytest.mark.django_db
def test_whatsapp_thread_links_guardian_when_no_patient_has_that_phone(clinic_a):
    mother = _patient(clinic_a, 'Rosa')
    link, _ = add_guardian(mother, first_name='Juan', phone=PHONE, relationship='child')
    session = get_or_create_session(clinic_a, '600 111 222')
    assert session.patient_id is None
    assert session.guardian == link.guardian


@pytest.mark.django_db
def test_archived_patient_rejects_new_appointments(clinic_a, patient_a, service_a, professional_a):
    patient_a.archive()
    with pytest.raises(PatientArchived):
        create_appointment(
            clinic=clinic_a, scheduled_at=timezone.now() + timedelta(days=3),
            require_online_booking=False, source=Appointment.Source.STAFF,
            service=service_a, professional=professional_a, patient=patient_a,
        )
    assert not Appointment.objects.filter(patient=patient_a).exists()


@pytest.mark.django_db
def test_panel_add_and_remove_contact(client, admin_user, patient_a):
    client.force_login(admin_user)
    response = client.post(
        reverse('patients:guardian-add', args=[patient_a.pk]),
        {'first_name': 'Juan', 'last_name': 'Doe', 'phone': PHONE, 'relationship': 'child'},
    )
    assert response.status_code == 302
    link = PatientGuardian.objects.get(patient=patient_a)

    detail = client.get(reverse('patients:detail', args=[patient_a.pk]))
    assert b'Guardar contacto' in detail.content and b'Juan Doe' in detail.content

    client.post(reverse('patients:guardian-remove', args=[patient_a.pk, link.pk]))
    assert not PatientGuardian.objects.filter(patient=patient_a).exists()
    assert Guardian.objects.filter(phone=PHONE).exists()


@pytest.mark.django_db
def test_other_clinic_patient_is_404(client, admin_user, patient_b):
    client.force_login(admin_user)
    response = client.post(
        reverse('patients:guardian-add', args=[patient_b.pk]),
        {'first_name': 'Juan', 'phone': PHONE, 'relationship': 'child'},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_create_patient_from_guardian(client, admin_user, patient_a):
    client.force_login(admin_user)
    link, _ = add_guardian(patient_a, first_name='Juan', last_name='Doe', phone=PHONE, relationship='child')
    url = reverse('patients:guardian-create-patient', args=[patient_a.pk, link.pk])

    response = client.post(url)

    link.guardian.refresh_from_db()
    son = link.guardian.patient
    assert son is not None and (son.first_name, son.last_name, son.phone) == ('Juan', 'Doe', PHONE)
    assert response.status_code == 302 and response.url == reverse('patients:detail', args=[son.pk])
    # sigue siendo contacto de la madre y ahora puede reservar para sí mismo
    assert set(link.guardian.bookable_patients()) == {patient_a, son}

    client.post(url)  # repetir no duplica
    assert Patient.objects.filter(phone=PHONE).count() == 1


@pytest.mark.django_db
def test_create_patient_from_guardian_links_existing_file(client, admin_user, patient_a, clinic_a):
    client.force_login(admin_user)
    existing = Patient.objects.create(clinic=clinic_a, first_name='Juan', last_name='X', phone=PHONE)
    link, _ = add_guardian(patient_a, first_name='Juan', phone=PHONE, relationship='child')
    link.guardian.patient = None
    link.guardian.save()

    client.post(reverse('patients:guardian-create-patient', args=[patient_a.pk, link.pk]))

    link.guardian.refresh_from_db()
    assert link.guardian.patient == existing
    assert Patient.objects.filter(phone=PHONE).count() == 1
