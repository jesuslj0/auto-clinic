import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.managers import (
    AllObjectsManager,
    ProtectedRecordError,
    SoftDeleteManager,
)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Base abstracta para modelos que se borran lógicamente.

    En este proyecto hay datos que no se borran físicamente jamás (historia
    clínica: Ley 41/2002, RGPD art. 9). Este mixin, reutilizable por cualquier
    capa clínica futura, implementa el borrado lógico:

    - `deleted_at` es la ÚNICA fuente de verdad; `is_deleted` es property (no un
      campo booleano que pueda desincronizarse).
    - `objects` excluye los borrados; `all_objects` los incluye y es el
      `base_manager`, para que Django y las señales de `audit` nunca pierdan de
      vista una fila borrada.
    - `delete()` de instancia hace borrado lógico salvo que `can_be_deleted()`
      lo vete, y entonces lanza `ProtectedRecordError` (un veto NO se degrada a
      borrado lógico).
    - `can_be_deleted()` es el gancho de política que cada modelo sobreescribe.

    Django NO cascadea el borrado lógico: cada modelo con hijos que deban
    seguirle debe recorrerlos en su propio `delete()`.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        base_manager_name = 'all_objects'
        default_manager_name = 'objects'

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def can_be_deleted(self) -> bool:
        """Gancho de política. `True` por defecto; los modelos lo restringen."""
        return True

    def _soft_delete_update_fields(self):
        # `updated_at` solo existe si el modelo hereda de TimeStampedModel; con
        # update_fields Django no refresca los auto_now por su cuenta, hay que
        # nombrarlos. Se añade solo si el campo existe.
        fields = ['deleted_at']
        if any(f.name == 'updated_at' for f in self._meta.fields):
            fields.append('updated_at')
        return fields

    def delete(self, using=None, keep_parents=False):
        """Borrado lógico. Un veto NO se degrada: lanza excepción."""
        if not self.can_be_deleted():
            raise ProtectedRecordError(
                f'{type(self).__name__}(pk={self.pk}) no se puede borrar.'
            )
        if self.deleted_at is None:
            self.deleted_at = timezone.now()
            self.save(update_fields=self._soft_delete_update_fields())

    def restore(self):
        """Deshace un borrado lógico."""
        if self.deleted_at is not None:
            self.deleted_at = None
            self.save(update_fields=self._soft_delete_update_fields())

    def hard_delete(self, using=None, keep_parents=False):
        """Borrado físico real. Sin uso en la capa clínica; reservado a la purga."""
        return super().delete(using=using, keep_parents=keep_parents)


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
        verbose_name = 'clínica'
        verbose_name_plural = 'clínicas'

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
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'

    def save(self, *args, **kwargs):
        self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
