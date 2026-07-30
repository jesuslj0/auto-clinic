import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models

from core.models import Clinic, User
from patients.models import Patient
from services.models import Service

# Duración de una cita cuando no hay servicio asociado (citas creadas por el bot).
DEFAULT_DURATION_MINUTES = 30


class Professional(models.Model):
    class ProfessionalType(models.TextChoices):
        MEDICO = 'medico', 'Médico'
        DENTISTA = 'dentista', 'Dentista'
        PSICOLOGO = 'psicologo', 'Psicólogo'
        ENFERMERO = 'enfermero', 'Enfermero/a'
        FISIOTERAPEUTA = 'fisioterapeuta', 'Fisioterapeuta'
        NUTRICIONISTA = 'nutricionista', 'Nutricionista'
        PODOLOGO = 'podologo', 'Podólogo'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professional_profile')
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='professionals')
    services = models.ManyToManyField(Service, related_name='professionals', blank=True)
    professional_type = models.CharField(
        max_length=30,
        choices=ProfessionalType.choices,
        default=ProfessionalType.MEDICO,
    )
    photo = models.ImageField(upload_to='professional_photos/', blank=True)

    is_active = models.BooleanField(default=True, db_index=True)
    accepts_online_booking = models.BooleanField(default=True)
    buffer_minutes = models.PositiveSmallIntegerField(default=0)
    slot_granularity_minutes = models.PositiveSmallIntegerField(default=15)

    class Meta:
        db_table = 'professionals'
        ordering = ['user__first_name', 'user__last_name', 'user__email']
        verbose_name = 'profesional'
        verbose_name_plural = 'profesionales'

    def __str__(self):
        return self.user.get_full_name() or self.user.email


class ProfessionalSchedule(models.Model):
    """Horario recurrente semanal de un profesional.

    IMPORTANTE: `start_time` y `end_time` son hora LOCAL de la clínica, no UTC.
    Un horario recurrente no tiene UTC: el mismo "9:00" cae en +01:00 o +02:00
    según el horario de verano. La conversión a UTC ocurre en `services.py`, al
    materializar los slots de una fecha concreta con el timezone de la clínica.

    Un profesional puede tener VARIOS tramos el mismo día (jornada partida:
    9:00–14:00 + 16:00–20:00).
    """

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
        ordering = ['day_of_week', 'start_time']
        verbose_name = 'horario de profesional'
        verbose_name_plural = 'horarios de profesionales'
        constraints = [
            models.UniqueConstraint(
                fields=['professional', 'day_of_week', 'start_time'],
                name='uniq_prof_day_start',
            ),
        ]

    def __str__(self):
        return f'{self.professional} — {self.get_day_of_week_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}'

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': 'La hora de fin debe ser posterior a la hora de inicio.'})

        if not (self.is_active and self.professional_id and self.start_time and self.end_time):
            return

        overlapping = ProfessionalSchedule.objects.filter(
            professional_id=self.professional_id,
            day_of_week=self.day_of_week,
            is_active=True,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        ).exclude(pk=self.pk)

        conflict = overlapping.first()
        if conflict:
            raise ValidationError({
                'start_time': (
                    f'Este tramo se solapa con otro ya existente: '
                    f'{conflict.start_time:%H:%M}–{conflict.end_time:%H:%M}.'
                )
            })


class ProfessionalTimeOff(models.Model):
    """Excepciones puntuales al horario recurrente: vacaciones, bajas, reuniones.

    A diferencia de `ProfessionalSchedule`, aquí sí hay instantes concretos, así
    que los campos son `DateTimeField` en UTC. Al ser datetimes y no fechas,
    cubre tanto "toda la semana de vacaciones" como "el martes de 11 a 13 tengo
    una reunión".
    """

    class Reason(models.TextChoices):
        VACATION = 'vacation', 'Vacaciones'
        SICK_LEAVE = 'sick_leave', 'Baja'
        TRAINING = 'training', 'Formación'
        OTHER = 'other', 'Otro'

    professional = models.ForeignKey(
        Professional, on_delete=models.CASCADE, related_name='time_off'
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'professional_time_off'
        ordering = ['starts_at']
        verbose_name = 'ausencia de profesional'
        verbose_name_plural = 'ausencias de profesionales'
        indexes = [models.Index(fields=['professional', 'starts_at', 'ends_at'])]

    def __str__(self):
        return (
            f'{self.professional} — {self.get_reason_display()} '
            f'{self.starts_at:%Y-%m-%d %H:%M}–{self.ends_at:%Y-%m-%d %H:%M}'
        )

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'La fecha de fin debe ser posterior a la de inicio.'})


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
    # Toda cita nace ligada a un profesional, pero el campo es opcional en la
    # capa de entrada: si el cliente no lo manda (el agente de WhatsApp no lo
    # hace), `services.create_appointment()` lo auto-asigna. Nullable en BD por
    # el SET_NULL: borrar un profesional no debe borrar su historial de citas.
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

    class Source(models.TextChoices):
        AGENT = 'agent', 'Agente WhatsApp'
        STAFF = 'staff', 'Panel'
        BOOKING = 'booking', 'Reserva pública'

    scheduled_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # De dónde vino la cita. Lo fija SIEMPRE la capa que llama al service (API,
    # form del panel, admin), nunca el propio service: `services.py` no sabe de
    # HTTP. Decide dos cosas: en qué estado nace la cita y si lleva hold.
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.STAFF, db_index=True
    )

    # Cuándo el PACIENTE dijo "SÍ" al recordatorio. Es un HECHO REGISTRADO, no un
    # estado: escribirlo NO cambia `status`, y esa asimetría es intencionada.
    #
    #   Paciente dice "SÍ" → patient_confirmed_at = now(). El hueco ya estaba
    #     bloqueado antes y sigue bloqueado después: no libera ni ocupa nada, así
    #     que no hay transición que registrar.
    #   Paciente dice "NO" → status = cancelled. Esto SÍ es una transición,
    #     porque libera un recurso.
    #
    # `confirmed` significa una sola cosa: "la clínica tiene la cita en firme".
    # Que el paciente reconfirme asistencia es un eje ortogonal. No los vuelvas a
    # juntar: es exactamente el bug que este campo vino a cerrar.
    patient_confirmed_at = models.DateTimeField(null=True, blank=True)

    # Hasta cuándo se le guarda el hueco a una cita que aún no ha validado el
    # staff. Solo lo llevan las citas que nacen `pending` por una vía no
    # presencial (agente, reserva pública). Al validarla, `confirm_by_clinic()`
    # lo pone a None: una cita en firme no caduca.
    hold_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
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
        verbose_name = 'cita'
        verbose_name_plural = 'citas'
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
            return self.scheduled_at + timedelta(minutes=self.service.booking_duration_minutes)
        return self.scheduled_at + timedelta(minutes=DEFAULT_DURATION_MINUTES)

    @classmethod
    def find_overlap(cls, professional, start, end, exclude_pk=None, statuses=None):
        """
        Devuelve la primera cita del profesional que se solapa con el rango
        [start, end), o None si no existe conflicto.

        Qué estados ocupan un hueco lo decide `BLOCKING_STATUSES`, la misma
        constante que lee el motor de disponibilidad: si aquí se bloqueara por
        estados distintos, la API rechazaría huecos que ella misma acaba de
        ofrecer. Se puede acotar con `statuses` cuando haga falta otra regla.
        """
        if statuses is None:
            statuses = BLOCKING_STATUSES

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

        if self.scheduled_at and self.status in LIVE_STATUSES:
            end = self.get_end_datetime()
            conflict = self.find_overlap(self.professional, self.scheduled_at, end, exclude_pk=self.pk)
            if conflict:
                raise ValidationError({
                    'scheduled_at': (
                        f'El profesional ya tiene una cita confirmada que se solapa: '
                        f'{conflict} ({conflict.scheduled_at:%H:%M}–{conflict.get_end_datetime():%H:%M}).'
                    )
                })


# Estados que ocupan un hueco en la agenda. Una cita 'pending' YA lo ocupa: en
# cuanto el agente reserva, el hueco se cierra para todos los demás. Antes no era
# así, y el resultado era que N pacientes podían reservar las 9:00 del día 20 y a
# todos se les decía por escrito que su cita estaba hecha; el día 19 se lo
# quedaba el primero que respondiera al recordatorio y al resto se le cancelaba.
#
# Lo que evita que un 'pending' bloquee el hueco para siempre es el hold
# (`hold_expires_at`): si el staff no la valida a tiempo, se cancela y el hueco
# se libera. Ver `expire_appointment_holds`.
#
# Fuente de verdad ÚNICA de la regla de bloqueo: la leen tanto `find_overlap()`
# como el motor de disponibilidad (`services.py` la reexporta). Vive aquí, y no
# en services.py, porque models.py no puede importar de services.py sin crear un
# ciclo. Léela, no la redefinas.
BLOCKING_STATUSES = frozenset({Appointment.Status.PENDING, Appointment.Status.CONFIRMED})

# Estados en los que una cita sigue VIVA: ni cancelada, ni completada, ni no_show.
# Es un eje distinto al de bloqueo (hoy una cita viva puede no bloquear), y
# estaba copiada a mano en media docena de sitios. Se usa para decidir si hay que
# revalidar su elegibilidad y si sigue teniendo sentido actuar sobre ella.
LIVE_STATUSES = frozenset({Appointment.Status.PENDING, Appointment.Status.CONFIRMED})


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
        verbose_name = 'cambio de estado de cita'
        verbose_name_plural = 'cambios de estado de citas'

    def __str__(self):
        return f'{self.appointment} | {self.from_status} → {self.to_status} ({self.actor})'
