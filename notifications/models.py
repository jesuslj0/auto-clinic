from django.conf import settings
from django.db import models
from django.utils import timezone

from appointments.models import Appointment
from core.models import Clinic, TimeStampedModel


class Reminder(TimeStampedModel):
    class ReminderType(models.TextChoices):
        H24 = '24h', '24 Hours'
        H2 = '2h', '2 Hours'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='reminders')
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='reminders')
    reminder_type = models.CharField(max_length=10, choices=ReminderType.choices)
    scheduled_for = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)

    class Meta:
        ordering = ['scheduled_for']
        unique_together = ('appointment', 'reminder_type')
        verbose_name = 'recordatorio'
        verbose_name_plural = 'recordatorios'

    def __str__(self):
        return f'{self.appointment} - {self.reminder_type}'


class Notification(models.Model):
    """Aviso in-app para un usuario del panel. Una fila por destinatario.

    NO es dato clínico y no está en la auditoría: el texto es siempre genérico
    («Nueva cita pendiente»), nunca lleva nombre de paciente ni texto de mensajes.
    El push sale por servicios de terceros (Google/Apple): ampliar lo que lleva el
    aviso es una decisión de privacidad.
    """

    class Kind(models.TextChoices):
        APPOINTMENT_PENDING = 'appointment_pending', 'Cita pendiente de validar'
        APPOINTMENT_RESCHEDULED = 'appointment_rescheduled', 'Cita reagendada'
        APPOINTMENT_CANCELLED = 'appointment_cancelled', 'Cita cancelada'
        CHAT_MESSAGE = 'chat_message', 'Mensaje nuevo'
        CHAT_NEW = 'chat_new', 'Conversación nueva'
        MESSAGE_FAILED = 'message_failed', 'Mensaje no entregado'
        PATIENT_FORM_COMPLETED = 'patient_form_completed', 'Ficha completada por el paciente'
        TEMPLATE_STATUS = 'template_status', 'Plantilla de WhatsApp revisada'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='notifications')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=200, blank=True)
    url = models.CharField(max_length=300, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    count = models.PositiveIntegerField(default=1)
    read_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'notificación'
        verbose_name_plural = 'notificaciones'
        indexes = [models.Index(fields=['user', 'read_at', '-created_at'], name='notif_user_read_idx')]

    def __str__(self):
        return f'{self.get_kind_display()} → {self.user_id}'

    @property
    def is_read(self):
        return self.read_at is not None


class NotificationPreference(models.Model):
    """Preferencia de un usuario por categoría. Sin fila = todo activo."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preferences'
    )
    kind = models.CharField(max_length=40, choices=Notification.Kind.choices)
    in_app = models.BooleanField(default=True)
    push = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'kind'], name='notif_pref_user_kind_uniq')
        ]

    def __str__(self):
        return f'{self.user_id}:{self.kind}'


class PushSubscription(models.Model):
    """Suscripción Web Push de un dispositivo. Se borra al recibir 404/410."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_subscriptions'
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'push {self.user_id} ({self.endpoint[:40]}…)'
