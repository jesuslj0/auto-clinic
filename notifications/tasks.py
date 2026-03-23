from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone

from appointments.models import Appointment
from notifications.models import Reminder


@shared_task
def send_appointment_reminder(reminder_id):
    reminder = Reminder.objects.select_related('appointment__patient', 'appointment__clinic').get(id=reminder_id)
    appointment = reminder.appointment
    patient = appointment.patient
    send_mail(
        subject=f'Reminder: appointment at {appointment.clinic.name}',
        message=(
            f'Hello {patient.first_name}, this is your {reminder.reminder_type} reminder '
            f'for {appointment.scheduled_at:%Y-%m-%d %H:%M UTC}.'
        ),
        from_email='no-reply@clinic.local',
        recipient_list=[patient.email] if patient.email else [],
        fail_silently=True,
    )
    reminder.sent_at = timezone.now()
    reminder.success = True
    reminder.save(update_fields=['sent_at', 'success', 'updated_at'])


def _dispatch_window(hours):
    now = timezone.now()
    target_start = now + timedelta(hours=hours)
    target_end = target_start + timedelta(minutes=30)
    reminder_type = Reminder.ReminderType.H24 if hours == 24 else Reminder.ReminderType.H2
    appointments = Appointment.objects.filter(
        status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        scheduled_at__gte=target_start,
        scheduled_at__lt=target_end,
    ).select_related('patient', 'clinic')

    for appointment in appointments:
        reminder, created = Reminder.objects.get_or_create(
            clinic=appointment.clinic,
            appointment=appointment,
            reminder_type=reminder_type,
            defaults={'scheduled_for': appointment.scheduled_at - timedelta(hours=hours)},
        )
        if created or not reminder.sent_at:
            send_appointment_reminder.delay(reminder.id)


@shared_task
def dispatch_24h_reminders():
    _dispatch_window(24)


@shared_task
def dispatch_2h_reminders():
    _dispatch_window(2)
