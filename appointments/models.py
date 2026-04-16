import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


class Professional(models.Model):
    class ProfessionalType(models.TextChoices):
        MEDICO = 'medico', 'Médico'
        DENTISTA = 'dentista', 'Dentista'
        PSICOLOGO = 'psicologo', 'Psicólogo'
        ENFERMERO = 'enfermero', 'Enfermero/a'
        FISIOTERAPEUTA = 'fisioterapeuta', 'Fisioterapeuta'
        NUTRICIONISTA = 'nutricionista', 'Nutricionista'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professional_profile')
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='professionals')
    services = models.ManyToManyField(Service, related_name='professionals', blank=True)
    professional_type = models.CharField(
        max_length=30,
        choices=ProfessionalType.choices,
        default=ProfessionalType.MEDICO,
    )

    class Meta:
        db_table = 'professionals'
        ordering = ['user__first_name', 'user__last_name', 'user__email']

    def __str__(self):
        return self.user.get_full_name() or self.user.email


class ProfessionalSchedule(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, 'Lunes'
        TUESDAY = 1, 'Martes'
        WEDNESDAY = 2, 'Miércoles'
        THURSDAY = 3, 'Jueves'
        FRIDAY = 4, 'Viernes'
        SATURDAY = 5, 'Sábado'
        SUNDAY = 6, 'Domingo'

    professional = models.ForeignKey(Professional, on_delete=models.CASCADE, related_name='schedules')
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'professional_schedules'
        unique_together = [('professional', 'day_of_week')]
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        return f'{self.professional} — {self.get_day_of_week_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}'

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': 'La hora de fin debe ser posterior a la hora de inicio.'})


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
