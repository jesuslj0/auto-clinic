import uuid
from datetime import timedelta

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
    photo = models.ImageField(upload_to='professional_photos/', blank=True)

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
        COMPLETED = 'completed', 'Completada'
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

    class CancelledBy(models.TextChoices):
        PATIENT = 'patient', 'Paciente'
        STAFF = 'staff', 'Clínica'

    scheduled_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    cancelled_by = models.CharField(
        max_length=10,
        choices=CancelledBy.choices,
        null=True,
        blank=True,
    )
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

    # ------------------------------------------------------------------
    # Helpers de tiempo
    # ------------------------------------------------------------------

    def get_end_datetime(self):
        """Devuelve la hora de fin real de la cita."""
        if self.end_at:
            return self.end_at
        if self.service_id and self.service:
            return self.scheduled_at + timedelta(minutes=self.service.duration_minutes)
        return self.scheduled_at + timedelta(minutes=30)

    @classmethod
    def find_overlap(cls, professional, start, end, exclude_pk=None, statuses=None):
        """
        Devuelve la primera cita del profesional que se solapa con el rango
        [start, end), o None si no existe conflicto.

        Por defecto considera citas en estado PENDING o CONFIRMED. Se puede
        acotar con `statuses` (p. ej. solo CONFIRMED al confirmar una cita,
        para permitir varias pendientes en el mismo tramo).
        """
        if statuses is None:
            statuses = [cls.Status.PENDING, cls.Status.CONFIRMED]

        # Candidatas: empiezan antes de que termine la nueva
        candidates = (
            cls.objects
            .filter(
                professional=professional,
                status__in=statuses,
                scheduled_at__lt=end,
            )
            .select_related('service')
        )
        if exclude_pk:
            candidates = candidates.exclude(pk=exclude_pk)

        for appt in candidates:
            if appt.get_end_datetime() > start:
                return appt
        return None

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    def clean(self):
        super().clean()
        if not self.professional or not self.service:
            return

        if self.professional.clinic_id != self.clinic_id:
            raise ValidationError({'professional': 'The selected professional does not belong to this clinic.'})

        if not self.professional.services.filter(pk=self.service_id).exists():
            raise ValidationError({'professional': 'The selected professional does not provide the selected service.'})

        if self.scheduled_at and self.status in (self.Status.PENDING, self.Status.CONFIRMED):
            end = self.get_end_datetime()
            conflict = self.find_overlap(self.professional, self.scheduled_at, end, exclude_pk=self.pk)
            if conflict:
                raise ValidationError({
                    'scheduled_at': (
                        f'El profesional ya tiene una cita activa que se solapa: '
                        f'{conflict} ({conflict.scheduled_at:%H:%M}–{conflict.get_end_datetime():%H:%M}).'
                    )
                })


class AppointmentStatusHistory(models.Model):
    """Registro inmutable de cada cambio de estado en una cita."""

    class Actor(models.TextChoices):
        PATIENT = 'patient', 'Paciente'
        STAFF = 'staff', 'Clínica'
        SYSTEM = 'system', 'Sistema'

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name='status_history',
    )
    from_status = models.CharField(max_length=20, choices=Appointment.Status.choices, null=True, blank=True)
    to_status = models.CharField(max_length=20, choices=Appointment.Status.choices)
    actor = models.CharField(max_length=10, choices=Actor.choices, default=Actor.SYSTEM)
    actor_label = models.CharField(max_length=150, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'appointment_status_history'
        ordering = ['changed_at']

    def __str__(self):
        return f'{self.appointment} | {self.from_status} → {self.to_status} ({self.actor})'
