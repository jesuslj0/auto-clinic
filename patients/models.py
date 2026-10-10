from django.conf import settings
from django.db import models
from django.utils import timezone

from clinical.files import clinical_media_storage
from core.models import Clinic, SoftDeleteModel, TimeStampedModel
from patients.photos import patient_photo_upload_to


class Patient(TimeStampedModel):
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='patients')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    # Opcional solo para quien no tiene teléfono propio y se gestiona a través de
    # un contacto (`Guardian`); con teléfono, sigue siendo la identidad en WhatsApp.
    phone = models.CharField(max_length=32, blank=True)
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
        # Sin teléfono no hay nada que distinguir: varios pacientes sin móvil
        # pueden convivir (y sin correo, también).
        constraints = [
            models.UniqueConstraint(
                fields=['clinic', 'email', 'phone'],
                condition=~models.Q(phone=''),
                name='patient_unique_clinic_email_phone',
            ),
        ]
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


class Guardian(TimeStampedModel):
    """Contacto responsable: la persona cuyo teléfono usa uno o varios pacientes.

    Es la identidad en WhatsApp de quien gestiona las citas de otros (el hijo que
    pide por sus padres). El teléfono es único por clínica: un número, un contacto.

    No es un paciente ni ve datos clínicos: solo gestiona la logística de citas.
    `patient` lo enlaza con su propia ficha cuando también es paciente de la
    clínica (puede serlo o no); en ese caso también puede reservar para sí mismo.
    """

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='guardians')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    patient = models.OneToOneField(
        Patient,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='guardian_profile',
        help_text='Su propia ficha, si también es paciente.',
    )
    dependents = models.ManyToManyField(
        Patient, through='PatientGuardian', related_name='guardians'
    )

    class Meta:
        ordering = ['last_name', 'first_name']
        constraints = [
            # Teléfono opcional: solo los contactos que tienen uno son únicos por clínica.
            models.UniqueConstraint(
                fields=['clinic', 'phone'],
                condition=~models.Q(phone=''),
                name='guardian_unique_clinic_phone',
            ),
        ]
        verbose_name = 'contacto responsable'
        verbose_name_plural = 'contactos responsables'

    def __str__(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def bookable_patients(self):
        """Pacientes para los que este contacto puede reservar, y nadie más.

        Los vinculados a él y, si también es paciente, él mismo. Los archivados
        no: no admiten citas nuevas.
        """
        linked = self.links.values_list('patient_id', flat=True)
        ids = set(linked)
        if self.patient_id:
            ids.add(self.patient_id)
        return Patient.objects.filter(
            clinic_id=self.clinic_id, pk__in=ids, archived_at__isnull=True
        )


class PatientGuardian(SoftDeleteModel, TimeStampedModel):
    """Vínculo entre un paciente y uno de sus contactos responsables.

    «Quitar» un contacto es borrado lógico: el vínculo deja de contar (los
    gestores por defecto lo excluyen) pero la fila queda como historial de quién
    pudo gestionar las citas de quién. Por eso el acceso va siempre por
    `guardian_links` / `links` / `objects`; **no** por el M2M `dependents` /
    `guardians`, que recorre la tabla intermedia sin mirar `deleted_at`.
    """

    class Relationship(models.TextChoices):
        CHILD = 'child', 'Hijo/a'
        SPOUSE = 'spouse', 'Cónyuge o pareja'
        PARENT = 'parent', 'Padre/madre'
        SIBLING = 'sibling', 'Hermano/a'
        CAREGIVER = 'caregiver', 'Cuidador/a'
        OTHER = 'other', 'Otro'

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='guardian_links')
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name='links')
    relationship = models.CharField(
        max_length=20, choices=Relationship.choices, default=Relationship.OTHER
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        constraints = [
            # Solo entre vínculos vivos: tras quitar uno se puede volver a añadir.
            models.UniqueConstraint(
                fields=['patient', 'guardian'],
                condition=models.Q(deleted_at__isnull=True),
                name='patientguardian_unique',
            ),
        ]
        verbose_name = 'vínculo paciente-contacto'
        verbose_name_plural = 'vínculos paciente-contacto'

    # Lo que el contacto ES respecto al paciente, en frase: «Es hijo/a de X».
    RELATIONSHIP_PHRASES = {
        'child': 'Es hijo/a de',
        'spouse': 'Es cónyuge o pareja de',
        'parent': 'Es padre/madre de',
        'sibling': 'Es hermano/a de',
        'caregiver': 'Es cuidador/a de',
        'other': 'Es contacto de',
    }

    @property
    def relationship_phrase(self):
        return self.RELATIONSHIP_PHRASES.get(self.relationship, 'Es contacto de')

    def clean(self):
        if self.patient_id and self.guardian_id and self.patient.clinic_id != self.guardian.clinic_id:
            from django.core.exceptions import ValidationError

            raise ValidationError('El paciente y el contacto deben ser de la misma clínica.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.guardian} → {self.patient}'
