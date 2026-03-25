import uuid
from django.db import models
from core.models import Clinic, TimeStampedModel, User
from patients.models import Patient
from services.models import Service


class Appointment(models.Model):
    STATUS_CHOICES = [
        ("scheduled", "Programada"),
        ("confirmed", "Confirmada"),
        ("cancelled", "Cancelada"),
        ("rescheduled", "Reprogramada"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, db_column="clinic_id")
    patient_phone = models.CharField(max_length=20)
    patient_name = models.CharField(max_length=255, blank=True)
    service = models.CharField(max_length=255, blank=True)
    datetime = models.DateTimeField()
    external_id = models.CharField(max_length=255, blank=True)
    external_calendar_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")

    # Recordatorio 24h
    reminder_24h_sent = models.BooleanField(default=False)
    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_responded = models.BooleanField(default=False)

    # Recordatorio 3h
    reminder_3h_sent = models.BooleanField(default=False)
    reminder_3h_sent_at = models.DateTimeField(null=True, blank=True)

    cancellation_policy_hours = models.IntegerField(default=24)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "appointments"
        indexes = [
            models.Index(fields=["clinic", "datetime"], name="idx_appointments_clinic_dt"),
            models.Index(
                fields=["patient_phone", "status", "datetime"],
                name="idx_appointments_phone_status_dt",
            ),
            models.Index(
                fields=["reminder_24h_sent", "reminder_responded", "reminder_3h_sent"],
                name="idx_appointments_reminder_flags",
            ),
        ]

    def __str__(self):
        return f"{self.patient_name} – {self.service} – {self.datetime:%Y-%m-%d %H:%M}"
