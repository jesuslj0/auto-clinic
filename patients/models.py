from django.conf import settings
from django.db import models
from django.utils import timezone

from clinical.files import clinical_media_storage
from core.models import Clinic, TimeStampedModel
from patients.photos import patient_photo_upload_to


class Patient(TimeStampedModel):
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='patients')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32)
    date_of_birth = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # Foto de perfil, en el bucket PRIVADO (ver `patients/photos.py`): se guarda
    # la clave del objeto y se sirve solo firmada. No sale por la API.
    photo = models.FileField(
        storage=clinical_media_storage,
        upload_to=patient_photo_upload_to,
        max_length=255,
        blank=True,
    )

    # Archivar aparta la ficha del directorio, del buscador y de los selectores,
    # pero NO la borra: historia, citas y facturas siguen donde estaban. Es
    # reversible (`restore()`); el borrado real es otra cosa y no vive en el panel.
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['last_name', 'first_name']
        unique_together = ('clinic', 'email', 'phone')
        verbose_name = 'paciente'
        verbose_name_plural = 'pacientes'

    def __str__(self):
        return f'{self.first_name} {self.last_name}'

    @property
    def is_archived(self):
        return self.archived_at is not None

    def archive(self, *, by=None):
        """Aparta la ficha. Con `save()` y no `update()`: así queda en `ChangeLog`."""
        if self.archived_at is None:
            self.archived_at = timezone.now()
            self.archived_by = by
            self.save(update_fields=['archived_at', 'archived_by', 'updated_at'])

    def restore(self):
        if self.archived_at is not None:
            self.archived_at = None
            self.archived_by = None
            self.save(update_fields=['archived_at', 'archived_by', 'updated_at'])