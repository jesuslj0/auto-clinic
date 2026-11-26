import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from clinical.files import clinical_media_storage
from core.models import TimeStampedModel
from exports.files import export_upload_to

#: Días que el ZIP generado se conserva en el bucket antes de borrarse.
DEFAULT_RETENTION_DAYS = 7


def retention_days() -> int:
    """Override: `EXPORT_RETENTION_DAYS` en settings."""
    return getattr(settings, 'EXPORT_RETENTION_DAYS', DEFAULT_RETENTION_DAYS)


class DataExport(TimeStampedModel):
    """Una petición de exportación de datos y el ZIP que produjo.

    La generación es asíncrona (Celery): la fila nace `pending`, la tarea la pasa
    a `running` y termina en `ready` o `failed`. El ZIP vive en el bucket privado
    `clinical_media` solo `EXPORT_RETENTION_DAYS` días: después se borra el objeto
    y la fila queda `expired` como constancia de que se hizo.

    Las etiquetas (`requested_by_label`, `patient_label`) congelan quién y de qué
    paciente fue, para que el historial siga siendo legible aunque el usuario o
    el paciente ya no existan.
    """

    class Scope(models.TextChoices):
        PATIENT = 'patient', 'Paciente'
        CLINIC = 'clinic', 'Clínica'

    class Status(models.TextChoices):
        PENDING = 'pending', 'En cola'
        RUNNING = 'running', 'Generando'
        READY = 'ready', 'Lista'
        FAILED = 'failed', 'Fallida'
        EXPIRED = 'expired', 'Caducada'

    #: Estados en los que la exportación sigue en marcha.
    ACTIVE_STATUSES = (Status.PENDING, Status.RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        'core.Clinic', on_delete=models.CASCADE, related_name='data_exports'
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    requested_by_label = models.CharField(max_length=255, blank=True)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    patient = models.ForeignKey(
        'patients.Patient',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    patient_label = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # Solo la clave del objeto (`exports/<2>/<uuid>.zip`), nunca una URL.
    file = models.FileField(
        storage=clinical_media_storage,
        upload_to=export_upload_to,
        max_length=255,
        blank=True,
    )
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=71, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    downloads = models.PositiveIntegerField(default=0)
    # Texto corto y genérico: nunca datos de salud ni trazas con contenido.
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Una sola exportación en marcha por paciente…
            models.UniqueConstraint(
                fields=['clinic', 'patient'],
                condition=Q(scope='patient', status__in=['pending', 'running']),
                name='exports_one_active_per_patient',
            ),
            # …y una sola de la clínica entera. Dos a la vez serían un volcado
            # duplicado de todo con la máquina compartida con n8n.
            models.UniqueConstraint(
                fields=['clinic'],
                condition=Q(scope='clinic', status__in=['pending', 'running']),
                name='exports_one_active_per_clinic',
            ),
        ]

    def __str__(self):
        target = self.patient_label or 'clínica'
        return f'Exportación {self.get_scope_display().lower()} ({target}) · {self.get_status_display()}'

    # --- estado -------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_downloadable(self) -> bool:
        """Lista, con fichero y dentro de plazo."""
        return self.status == self.Status.READY and bool(self.file) and not self.is_expired

    def mark_running(self):
        self.status = self.Status.RUNNING
        self.error = ''
        self.save(update_fields=['status', 'error', 'updated_at'])

    def mark_failed(self, message: str):
        self.status = self.Status.FAILED
        self.error = message[:255]
        self.save(update_fields=['status', 'error', 'updated_at'])
