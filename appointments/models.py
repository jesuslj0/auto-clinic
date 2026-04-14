import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


class Professional(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professional_profile')
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='professionals')
    services = models.ManyToManyField(Service, related_name='professionals', blank=True)
    role = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = 'professionals'
        ordering = ['user__first_name', 'user__last_name', 'user__email']

    def __str__(self):
        return self.user.get_full_name() or self.user.email


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        CONFIRMED = 'confirmed', 'Confirmada'
        CANCELLED = 'cancelled', 'Cancelada'
        RESCHEDULED = 'rescheduled', 'Reagendada'
        NO_SHOW = 'no_show', 'No asistió'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='appointments', db_column='clinic_id')

    # Structured relations (may be null for bot-created appointments)
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    professional = models.ForeignKey(
        Professional,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )

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
            models.Index(fields=['clinic', 'scheduled_at', 'status'], name='idx_appointments_clinic_status'),
            models.Index(
                fields=['patient_phone', 'status', 'scheduled_at'],
                name='idx_appointments_phone_status',
            ),
            models.Index(
                fields=['reminder_24h_sent', 'reminder_responded', 'reminder_3h_sent'],
                name='idx_appointments_reminder',
            ),
        ]

    def __str__(self):
        name = str(self.patient) if self.patient else self.patient_name
        return f'{name} - {self.scheduled_at:%Y-%m-%d %H:%M}'

    def clean(self):
        super().clean()
        if not self.professional or not self.service:
            return

        if self.professional.clinic_id != self.clinic_id:
            raise ValidationError({'professional': 'The selected professional does not belong to this clinic.'})

        if not self.professional.services.filter(pk=self.service_id).exists():
            raise ValidationError({'professional': 'The selected professional does not provide the selected service.'})
