import uuid

from django.db import models

from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'
        RESCHEDULED = 'rescheduled', 'Rescheduled'
        NO_SHOW = 'no_show', 'No Show'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='appointments', db_column='clinic_id')

    # Structured relations (may be null for bot-created appointments)
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')

    # Denormalized fields used by the WhatsApp bot
    patient_phone = models.CharField(max_length=20, blank=True)
    patient_name = models.CharField(max_length=255, blank=True)
    service_name = models.CharField(max_length=255, blank=True)

    scheduled_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    confirmation_token = models.UUIDField(default=uuid.uuid4, unique=True)

    # External calendar integration
    external_id = models.CharField(max_length=255, blank=True)
    external_calendar_id = models.CharField(max_length=255, blank=True)

    notes = models.TextField(blank=True)
    cancellation_policy_hours = models.IntegerField(default=24)

    # 24h reminder
    reminder_24h_sent = models.BooleanField(default=False)
    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_responded = models.BooleanField(default=False)

    # 3h reminder
    reminder_3h_sent = models.BooleanField(default=False)
    reminder_3h_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'appointments'
        ordering = ['scheduled_at']
        indexes = [
            models.Index(fields=['clinic', 'scheduled_at', 'status'], name='idx_appointments_clinic_dt_status'),
            models.Index(
                fields=['patient_phone', 'status', 'scheduled_at'],
                name='idx_appointments_phone_status_dt',
            ),
            models.Index(
                fields=['reminder_24h_sent', 'reminder_responded', 'reminder_3h_sent'],
                name='idx_appointments_reminder_flags',
            ),
        ]

    def __str__(self):
        name = str(self.patient) if self.patient else self.patient_name
        return f'{name} - {self.scheduled_at:%Y-%m-%d %H:%M}'
