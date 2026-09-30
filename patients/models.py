from django.db import models

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

    class Meta:
        ordering = ['last_name', 'first_name']
        unique_together = ('clinic', 'email', 'phone')
        verbose_name = 'paciente'
        verbose_name_plural = 'pacientes'

    def __str__(self):
        return f'{self.first_name} {self.last_name}'