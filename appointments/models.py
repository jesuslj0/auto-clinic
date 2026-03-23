import uuid

from django.db import models

from core.models import Clinic, TimeStampedModel, User
from patients.models import Patient
from services.models import Service


class Appointment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'
        NO_SHOW = 'no_show', 'No Show'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='appointments')
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='appointments')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='appointments')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_appointments')
    scheduled_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    confirmation_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['scheduled_at']
        indexes = [models.Index(fields=['clinic', 'scheduled_at', 'status'])]

    def __str__(self):
        return f'{self.patient} - {self.scheduled_at:%Y-%m-%d %H:%M}'
