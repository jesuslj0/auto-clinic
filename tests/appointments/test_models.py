"""Tests for Appointment model."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from appointments.models import Appointment


@pytest.mark.django_db
class TestAppointmentModel:
    def test_create_appointment(self, appointment_a):
        assert appointment_a.pk is not None
        assert appointment_a.status == Appointment.Status.PENDING
        assert appointment_a.confirmation_token is not None

    def test_confirmation_token_is_uuid(self, appointment_a):
        assert isinstance(appointment_a.confirmation_token, uuid.UUID)

    def test_confirmation_token_unique(self, db, clinic_a, patient_a, service_a):
        now = timezone.now() + timedelta(hours=10)
        a1 = Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=now, end_at=now + timedelta(minutes=30),
        )
        a2 = Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=now + timedelta(hours=2), end_at=now + timedelta(hours=2, minutes=30),
        )
        assert a1.confirmation_token != a2.confirmation_token

    def test_status_choices(self):
        choices = {c[0] for c in Appointment.Status.choices}
        assert {"pending", "confirmed", "cancelled", "no_show"} == choices

    def test_str_representation(self, appointment_a):
        expected = f"{appointment_a.patient} - {appointment_a.scheduled_at:%Y-%m-%d %H:%M}"
        assert str(appointment_a) == expected

    def test_ordering_by_scheduled_at(self, db, clinic_a, patient_a, service_a):
        now = timezone.now()
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=now + timedelta(hours=5),
            end_at=now + timedelta(hours=5, minutes=30),
        )
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=2, minutes=30),
        )
        times = list(
            Appointment.objects.filter(clinic=clinic_a).values_list("scheduled_at", flat=True)
        )
        assert times == sorted(times)
