"""Tests for notifications Celery tasks."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from appointments.models import Appointment
from notifications.models import Reminder
from notifications.tasks import (
    _dispatch_window,
    dispatch_24h_reminders,
    dispatch_2h_reminders,
    send_appointment_reminder,
)


@pytest.mark.django_db
class TestSendAppointmentReminderTask:
    def test_sends_email_to_patient(self, reminder_a, patient_a):
        patient_a.email = "patient@test.com"
        patient_a.save()
        reminder_a.appointment.patient.email = "patient@test.com"

        with patch("notifications.tasks.send_mail") as mock_send:
            send_appointment_reminder(reminder_a.pk)
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            assert "patient@test.com" in call_kwargs[1]["recipient_list"]

    def test_marks_reminder_as_sent(self, reminder_a):
        with patch("notifications.tasks.send_mail"):
            send_appointment_reminder(reminder_a.pk)

        reminder_a.refresh_from_db()
        assert reminder_a.success is True
        assert reminder_a.sent_at is not None

    def test_email_subject_contains_clinic_name(self, reminder_a, clinic_a):
        with patch("notifications.tasks.send_mail") as mock_send:
            send_appointment_reminder(reminder_a.pk)
            subject = mock_send.call_args[1]["subject"]
            assert clinic_a.name in subject

    def test_no_email_sent_when_patient_has_no_email(self, reminder_a, patient_a):
        patient_a.email = ""
        patient_a.save()

        with patch("notifications.tasks.send_mail") as mock_send:
            send_appointment_reminder(reminder_a.pk)
            # send_mail called with empty recipient_list — fail_silently prevents error
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["recipient_list"] == []


@pytest.mark.django_db
class TestDispatchWindow:
    def test_dispatch_24h_creates_reminder_for_upcoming_appointment(
        self, db, clinic_a, patient_a, service_a
    ):
        # Appointment in ~24h from now (within the 30-min window)
        now = timezone.now()
        scheduled = now + timedelta(hours=24, minutes=10)
        appointment = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=scheduled,
            end_at=scheduled + timedelta(minutes=30),
            status=Appointment.Status.PENDING,
        )

        with patch("notifications.tasks.send_appointment_reminder") as mock_task:
            mock_task.delay = lambda rid: None
            _dispatch_window(24)

        reminder = Reminder.objects.filter(
            appointment=appointment,
            reminder_type=Reminder.ReminderType.H24,
        ).first()
        assert reminder is not None

    def test_dispatch_does_not_create_duplicate_reminders(
        self, db, clinic_a, patient_a, service_a
    ):
        now = timezone.now()
        scheduled = now + timedelta(hours=24, minutes=10)
        appointment = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=scheduled,
            end_at=scheduled + timedelta(minutes=30),
            status=Appointment.Status.PENDING,
        )

        with patch("notifications.tasks.send_appointment_reminder") as mock_task:
            mock_task.delay = lambda rid: None
            _dispatch_window(24)
            _dispatch_window(24)  # Second run should not create duplicate

        count = Reminder.objects.filter(
            appointment=appointment,
            reminder_type=Reminder.ReminderType.H24,
        ).count()
        assert count == 1

    def test_cancelled_appointment_skipped(self, db, clinic_a, patient_a, service_a):
        now = timezone.now()
        scheduled = now + timedelta(hours=24, minutes=10)
        appointment = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=scheduled,
            end_at=scheduled + timedelta(minutes=30),
            status=Appointment.Status.CANCELLED,
        )

        with patch("notifications.tasks.send_appointment_reminder") as mock_task:
            mock_task.delay = lambda rid: None
            _dispatch_window(24)

        reminder = Reminder.objects.filter(appointment=appointment).first()
        assert reminder is None

    def test_dispatch_2h_creates_reminder(self, db, clinic_a, patient_a, service_a):
        now = timezone.now()
        scheduled = now + timedelta(hours=2, minutes=10)
        appointment = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=scheduled,
            end_at=scheduled + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        with patch("notifications.tasks.send_appointment_reminder") as mock_task:
            mock_task.delay = lambda rid: None
            _dispatch_window(2)

        reminder = Reminder.objects.filter(
            appointment=appointment,
            reminder_type=Reminder.ReminderType.H2,
        ).first()
        assert reminder is not None

    def test_appointment_outside_window_not_dispatched(self, db, clinic_a, patient_a, service_a):
        now = timezone.now()
        # 48h away — outside the 24h window
        scheduled = now + timedelta(hours=48)
        appointment = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=scheduled,
            end_at=scheduled + timedelta(minutes=30),
            status=Appointment.Status.PENDING,
        )

        with patch("notifications.tasks.send_appointment_reminder") as mock_task:
            mock_task.delay = lambda rid: None
            _dispatch_window(24)

        reminder = Reminder.objects.filter(appointment=appointment).first()
        assert reminder is None

    def test_dispatch_24h_reminders_task_is_callable(self):
        with patch("notifications.tasks._dispatch_window") as mock_dispatch:
            dispatch_24h_reminders()
            mock_dispatch.assert_called_once_with(24)

    def test_dispatch_2h_reminders_task_is_callable(self):
        with patch("notifications.tasks._dispatch_window") as mock_dispatch:
            dispatch_2h_reminders()
            mock_dispatch.assert_called_once_with(2)


@pytest.mark.django_db
class TestReminderModel:
    def test_create_reminder(self, reminder_a):
        assert reminder_a.pk is not None
        assert reminder_a.success is False
        assert reminder_a.sent_at is None

    def test_unique_together_appointment_reminder_type(self, db, clinic_a, appointment_a):
        from django.db import IntegrityError
        Reminder.objects.create(
            clinic=clinic_a,
            appointment=appointment_a,
            reminder_type=Reminder.ReminderType.H2,
            scheduled_for=appointment_a.scheduled_at - timedelta(hours=2),
        )
        with pytest.raises(IntegrityError):
            Reminder.objects.create(
                clinic=clinic_a,
                appointment=appointment_a,
                reminder_type=Reminder.ReminderType.H2,
                scheduled_for=appointment_a.scheduled_at - timedelta(hours=2),
            )

    def test_str_representation(self, reminder_a, appointment_a):
        assert str(reminder_a.appointment) in str(reminder_a)


@pytest.mark.django_db
class TestReminderViewSet:
    def test_list_reminders_scoped_to_clinic(self, admin_client, reminder_a, db, clinic_b, appointment_b):
        from notifications.models import Reminder
        from datetime import timedelta
        reminder_b = Reminder.objects.create(
            clinic=clinic_b,
            appointment=appointment_b,
            reminder_type=Reminder.ReminderType.H24,
            scheduled_for=appointment_b.scheduled_at - timedelta(hours=24),
        )
        response = admin_client.get("/api/reminders/")
        assert response.status_code == 200
        ids = [r["id"] for r in response.data["results"]]
        assert reminder_a.pk in ids
        assert reminder_b.pk not in ids

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/reminders/")
        assert response.status_code in (401, 403)
