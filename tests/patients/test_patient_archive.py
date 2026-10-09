"""Archivar fichas: solo admins, cancela citas futuras y sale de directorio y buscador."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment


def _appointment(patient, service, professional, *, days, status=Appointment.Status.CONFIRMED):
    start = timezone.now() + timedelta(days=days)
    return Appointment.objects.create(
        clinic=patient.clinic, patient=patient, service=service, professional=professional,
        scheduled_at=start, end_at=start + timedelta(minutes=30), status=status,
    )


@pytest.fixture
def admin_session(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
def test_archive_cancels_future_appointments_only(
    admin_session, patient_a, service_a, professional_a, admin_user
):
    future = _appointment(patient_a, service_a, professional_a, days=3)
    pending = _appointment(patient_a, service_a, professional_a, days=5, status=Appointment.Status.PENDING)
    past = _appointment(patient_a, service_a, professional_a, days=-3, status=Appointment.Status.COMPLETED)

    response = admin_session.post(reverse('patients:archive', args=[patient_a.pk]))

    assert response.status_code == 302
    patient_a.refresh_from_db()
    assert patient_a.is_archived
    assert patient_a.archived_by == admin_user
    for appt in (future, pending):
        appt.refresh_from_db()
        assert appt.status == Appointment.Status.CANCELLED
        assert appt.cancelled_by == Appointment.CancelledBy.STAFF
    past.refresh_from_db()
    assert past.status == Appointment.Status.COMPLETED


@pytest.mark.django_db
def test_staff_cannot_archive(client, staff_user, patient_a):
    client.force_login(staff_user)
    response = client.post(reverse('patients:archive', args=[patient_a.pk]))
    assert response.status_code == 403
    patient_a.refresh_from_db()
    assert not patient_a.is_archived


@pytest.mark.django_db
def test_other_clinic_patient_is_404(admin_session, patient_b):
    response = admin_session.post(reverse('patients:archive', args=[patient_b.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_archived_leaves_directory_search_and_returns_with_filter(admin_session, patient_a):
    admin_session.post(reverse('patients:archive', args=[patient_a.pk]))

    listing = admin_session.get(reverse('patients:list'))
    assert patient_a not in listing.context['patients']
    archived = admin_session.get(reverse('patients:list'), {'estado': 'archivados'})
    assert patient_a in archived.context['patients']

    found = admin_session.get(reverse('core:search'), {'q': patient_a.first_name})
    assert patient_a not in found.context['patients']


@pytest.mark.django_db
def test_detail_modal_lists_upcoming_appointments(
    admin_session, patient_a, service_a, professional_a
):
    _appointment(patient_a, service_a, professional_a, days=2)
    response = admin_session.get(reverse('patients:detail', args=[patient_a.pk]))
    assert len(response.context['upcoming_appointments']) == 1
    assert b'Archivar y cancelar citas' in response.content


@pytest.mark.django_db
def test_restore(admin_session, patient_a):
    patient_a.archive()
    admin_session.post(reverse('patients:restore', args=[patient_a.pk]))
    patient_a.refresh_from_db()
    assert not patient_a.is_archived
