import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Clinic(models.Model):
    clinic_id = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=255)
    nif = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    # Contacto
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # Dirección
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=2, default="ES")

    # Configuración
    timezone = models.CharField(max_length=50, default="Europe/Madrid")
    hold_ttl_minutes = models.PositiveIntegerField(
        default=1440,
        help_text=(
            "Minutos que una cita creada por el agente o la reserva pública queda "
            "reservada esperando validación del staff. Pasado ese plazo se cancela "
            "y el hueco se libera. 0 = sin caducidad."
        ),
    )
    description = models.TextField(blank=True)
    logo_url = models.URLField(blank=True)
    logo = models.ImageField(upload_to='clinic_logos/', blank=True)
    whatsapp_phone_number_id = models.CharField(max_length=100, blank=True)
    whatsapp_token = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text="Token de acceso permanente de la WhatsApp Cloud API",
    )
    whatsapp_verify_token = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Token de verificación del webhook de Meta",
    )
    agent_api_key = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="API Key única que usa n8n para autenticarse con esta clínica",
    )

    # Control del agente desde el panel de conversaciones
    agent_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Interruptor general del agente. Si se apaga, el agente deja de "
            "responder en todos los chats de la clínica y la atención pasa a ser manual."
        ),
    )
    agent_handoff_timeout_seconds = models.PositiveIntegerField(
        default=300,
        help_text=(
            "Segundos de inactividad del staff tras los que el agente retoma "
            "automáticamente una conversación puesta en modo humano."
        ),
    )
    conversation_retention_months = models.PositiveIntegerField(
        default=24,
        help_text="Meses que se conserva el histórico de conversaciones antes de purgarse. 0 = sin límite.",
    )

    # Integración de calendario
    api_type = models.CharField(
        max_length=20,
        choices=[
            ("calendly", "Calendly"),
            ("google_calendar", "Google Calendar"),
            ("custom", "Custom"),
        ],
        blank=True,
    )
    api_url = models.CharField(max_length=500, blank=True)
    api_key = models.CharField(max_length=500, blank=True)

    # Calendly
    calendly_link = models.CharField(max_length=500, blank=True)
    calendly_token = models.CharField(max_length=500, blank=True)
    calendly_event_type_uuid = models.UUIDField(null=True, blank=True)

    # Google Calendar
    google_calendar_id = models.CharField(max_length=255, blank=True)

    # Pruebas del agente
    test_patient = models.ForeignKey(
        'patients.Patient',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text="Paciente de prueba para testear el agente desde el panel",
    )

    class Meta:
        db_table = "clinics"

    def clean(self):
        super().clean()
        if self.test_patient and self.test_patient.clinic_id != self.clinic_id:
            raise ValidationError(
                {'test_patient': 'El paciente de prueba debe pertenecer a esta misma clínica.'}
            )

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, username=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser, TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        STAFF = 'staff', 'Staff'

    username = models.CharField(max_length=150, unique=True, blank=True)
    email = models.EmailField(unique=True)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='users', null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ['email']

    def save(self, *args, **kwargs):
        self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
