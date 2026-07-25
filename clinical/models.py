"""Núcleo clínico: historia, episodio, visita, nota SOAP y adenda.

Esta capa distingue tres estados de dato:

- **Editable** — borradores y datos administrativos, se modifican con normalidad.
- **Firmado / inmutable** — una nota firmada no se edita ni se borra jamás; solo
  admite adendas.
- **Conservado** — nada de esta capa se borra físicamente. El borrado es siempre
  lógico (`SoftDeleteModel`) y la conservación se cuenta por episodio.

La inmutabilidad de la nota firmada y de la adenda se defiende a DOS niveles: en
el modelo (aquí) y con un trigger de PostgreSQL (migración 0002). El de aquí es
la primera barrera, no la única.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from clinical.conf import retention_years
from clinical.exceptions import (
    EpisodeClosed,
    NoteAlreadySigned,
    ProtectedClinicalRecord,
)
from clinical.hashing import compute_note_hash
from clinical.managers import AppendOnlyInsertManager, next_history_number
from core.models import SoftDeleteModel, TimeStampedModel


def _add_years(dt, years):
    """Suma años a un datetime sin dependencias externas (maneja el 29-F)."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return dt.replace(month=2, day=28, year=dt.year + years)


class HistorySequence(models.Model):
    """Contador correlativo de historias por clínica y año.

    Infraestructura interna: NO es dato clínico y NO se audita. Existe solo para
    generar `MedicalHistory.number` de forma segura frente a altas concurrentes
    (ver `clinical.managers.next_history_number`).
    """

    clinic = models.ForeignKey('core.Clinic', on_delete=models.CASCADE, related_name='+')
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'clinical_history_sequence'
        unique_together = ('clinic', 'year')
        verbose_name = 'secuencia de numeración de historias'
        verbose_name_plural = 'secuencias de numeración de historias'

    def __str__(self):
        return f'{self.clinic_id} {self.year}: {self.last_value}'


class MedicalHistory(SoftDeleteModel, TimeStampedModel):
    """Historia clínica. 1:1 con el paciente; no se borra nunca.

    Los FK a `Patient` y `Clinic` son `DO_NOTHING` + `db_constraint=False`, el
    mismo patrón que los registros de `audit`: borrar un paciente (o su clínica)
    NO arrastra ni bloquea su historia. La fila sobrevive con el `patient_id`
    colgando, y así la conservación que exige la Ley 41/2002 se cumple sin
    cambiar el comportamiento de borrado de la capa administrativa. Una historia
    huérfana de un paciente ya borrado es aceptable y previsto: es justo el
    escenario de conservación de registros de antiguos pacientes.
    """

    patient = models.OneToOneField(
        'patients.Patient', on_delete=models.DO_NOTHING,
        related_name='medical_history', db_constraint=False,
    )
    clinic = models.ForeignKey(
        'core.Clinic', on_delete=models.DO_NOTHING,
        related_name='medical_histories', db_constraint=False,
    )
    number = models.CharField(max_length=20, help_text='Número de historia, p. ej. HC-2026-00001.')
    opened_at = models.DateTimeField(default=timezone.now)

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_medical_history'
        unique_together = ('clinic', 'number')
        ordering = ['number']
        verbose_name = 'historia clínica'
        verbose_name_plural = 'historias clínicas'

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_history_number(self.clinic)
        super().save(*args, **kwargs)

    def can_be_deleted(self) -> bool:
        # La historia clínica no se borra jamás, ni siquiera lógicamente.
        return False

    def delete(self, using=None, keep_parents=False):
        raise ProtectedClinicalRecord(
            f'La historia clínica {self.number} no se puede borrar.'
        )


class Episode(SoftDeleteModel, TimeStampedModel):
    """Proceso asistencial. Unidad sobre la que se cuenta la conservación."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Abierto'
        CLOSED = 'closed', 'Cerrado'

    history = models.ForeignKey(
        MedicalHistory, on_delete=models.PROTECT, related_name='episodes'
    )
    reason = models.TextField(help_text='Motivo de consulta.')
    opened_at = models.DateTimeField(default=timezone.now)
    discharged_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    responsible_professional = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_episode'
        ordering = ['-opened_at']
        verbose_name = 'episodio'
        verbose_name_plural = 'episodios'

    def __str__(self):
        return f'Episodio #{self.pk} ({self.get_status_display()})'

    def close(self):
        """Cierra el episodio y fija la fecha de alta."""
        if self.status == self.Status.CLOSED:
            return
        self.status = self.Status.CLOSED
        self.discharged_at = timezone.now()
        self.save(update_fields=['status', 'discharged_at', 'updated_at'])

    def reopen(self):
        """Reabre un episodio cerrado. La reapertura queda en el ChangeLog."""
        if self.status == self.Status.OPEN:
            return
        self.status = self.Status.OPEN
        self.discharged_at = None
        self.save(update_fields=['status', 'discharged_at', 'updated_at'])

    @property
    def retention_expires_at(self):
        """Fecha en que expira el plazo de conservación, o `None` si sigue abierto.

        Andamiaje: se calcula, pero NADA lo purga automáticamente. El plazo es un
        setting (`clinical.conf.retention_years`) precisamente porque depende de
        la normativa autonómica, pendiente de confirmar.
        """
        if self.discharged_at is None:
            return None
        return _add_years(self.discharged_at, retention_years())

    def _has_signed_notes(self) -> bool:
        return ClinicalNote.all_objects.filter(
            visit__episode=self, status=ClinicalNote.Status.SIGNED
        ).exists()

    def can_be_deleted(self) -> bool:
        # Un episodio con actividad clínica firmada es parte del registro
        # permanente: no se borra ni lógicamente.
        return not self._has_signed_notes()

    def delete(self, using=None, keep_parents=False):
        if not self.can_be_deleted():
            raise ProtectedClinicalRecord(
                f'El episodio #{self.pk} tiene notas firmadas y no se puede borrar.'
            )
        # Django no cascadea el borrado lógico: se recorre a mano. Como no hay
        # notas firmadas debajo (lo garantiza can_be_deleted), la cascada solo
        # alcanza borradores.
        for visit in Visit.all_objects.filter(episode=self, deleted_at__isnull=True):
            visit.delete()
        super().delete(using=using, keep_parents=keep_parents)


class Visit(SoftDeleteModel, TimeStampedModel):
    """Encuentro clínico que realmente ocurrió. Distinta de `Appointment`.

    Una visita puede existir sin cita (urgencia) y una cita puede no acabar en
    visita (no-show); por eso la FK a `Appointment` es opcional. El agente de n8n
    escribe sobre `Appointment` pero NO tiene ningún camino hasta aquí.
    """

    episode = models.ForeignKey(Episode, on_delete=models.PROTECT, related_name='visits')
    professional = models.ForeignKey(
        'appointments.Professional', on_delete=models.PROTECT, related_name='+'
    )
    appointment = models.ForeignKey(
        'appointments.Appointment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='visits',
    )
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_visit'
        ordering = ['-occurred_at']
        verbose_name = 'visita'
        verbose_name_plural = 'visitas'

    def __str__(self):
        return f'Visita #{self.pk} ({self.occurred_at:%Y-%m-%d %H:%M})'

    def clean(self):
        super().clean()
        if self._state.adding and self.episode_id and self.episode.status == Episode.Status.CLOSED:
            raise ValidationError(
                'No se pueden registrar visitas en un episodio cerrado; reábrelo primero.'
            )

    def save(self, *args, **kwargs):
        # Barrera dura, más allá de los formularios: un episodio cerrado no
        # admite visitas nuevas sin reabrirse explícitamente.
        if self._state.adding and self.episode_id and self.episode.status == Episode.Status.CLOSED:
            raise EpisodeClosed(
                f'El episodio #{self.episode_id} está cerrado y no admite visitas nuevas.'
            )
        super().save(*args, **kwargs)

    def _has_signed_notes(self) -> bool:
        return ClinicalNote.all_objects.filter(
            visit=self, status=ClinicalNote.Status.SIGNED
        ).exists()

    def can_be_deleted(self) -> bool:
        return not self._has_signed_notes()

    def delete(self, using=None, keep_parents=False):
        if not self.can_be_deleted():
            raise ProtectedClinicalRecord(
                f'La visita #{self.pk} tiene notas firmadas y no se puede borrar.'
            )
        for note in ClinicalNote.all_objects.filter(visit=self, deleted_at__isnull=True):
            note.delete()
        super().delete(using=using, keep_parents=keep_parents)


class ClinicalNote(SoftDeleteModel, TimeStampedModel):
    """Nota clínica SOAP. Borrador editable → firmada inmutable."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        SIGNED = 'signed', 'Firmada'

    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='notes')
    subjective = models.TextField(blank=True, help_text='S: lo que refiere el paciente.')
    objective = models.TextField(blank=True, help_text='O: hallazgos de la exploración.')
    assessment = models.TextField(blank=True, help_text='A: análisis / juicio clínico.')
    plan = models.TextField(blank=True, help_text='P: plan de actuación.')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    signed_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(
        max_length=100, blank=True,
        help_text='sha256:<hexdigest> del contenido SOAP y los metadatos de firma.',
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_note'
        ordering = ['-created_at']
        verbose_name = 'nota clínica'
        verbose_name_plural = 'notas clínicas'

    def __str__(self):
        return f'Nota #{self.pk} ({self.get_status_display()})'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Estado con el que se cargó de la base de datos: es la referencia para
        # decidir si esta nota ya era inmutable ANTES de este save().
        instance._loaded_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        # Una nota que ya estaba firmada en la base de datos es inmutable: ni un
        # campo de contenido ni el propio estado admiten UPDATE. La transición
        # borrador→firmada sí pasa, porque en ese momento `_loaded_status` aún es
        # 'draft'.
        if getattr(self, '_loaded_status', None) == self.Status.SIGNED:
            raise NoteAlreadySigned(
                f'La nota #{self.pk} está firmada y no admite modificación. '
                f'El único cambio posible es añadir una adenda.'
            )
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    def sign(self, professional):
        """Firma la nota: fija firmante, fecha y hash, y la vuelve inmutable."""
        if self.status == self.Status.SIGNED:
            raise NoteAlreadySigned(f'La nota #{self.pk} ya está firmada.')
        if self._state.adding:
            # El hash ata el contenido al id de la nota; necesita pk. Si aún no
            # se ha persistido, se guarda primero como borrador.
            self.save()
        self.signed_by = professional
        self.signed_at = timezone.now()
        self.status = self.Status.SIGNED
        self.content_hash = compute_note_hash(self)
        self.save(update_fields=[
            'signed_by', 'signed_at', 'status', 'content_hash', 'updated_at',
        ])

    def can_be_deleted(self) -> bool:
        # Una nota firmada no se borra de ninguna forma: ni lógica, ni física, ni
        # en cascada. Un borrador sí admite borrado lógico.
        return self.status != self.Status.SIGNED


class Addendum(TimeStampedModel):
    """Adenda a una nota clínica. De solo inserción, como la auditoría.

    Es la única forma de añadir algo a una nota firmada. No se edita ni se borra,
    y no altera la nota original ni su hash.
    """

    note = models.ForeignKey(ClinicalNote, on_delete=models.PROTECT, related_name='addenda')
    author = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    text = models.TextField(help_text='Contenido de la adenda.')

    objects = AppendOnlyInsertManager()

    class Meta:
        db_table = 'clinical_addendum'
        ordering = ['created_at']
        verbose_name = 'adenda'
        verbose_name_plural = 'adendas'

    def __str__(self):
        return f'Adenda #{self.pk} a nota #{self.note_id}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ProtectedClinicalRecord(
                f'La adenda #{self.pk} es de solo inserción: no se puede modificar.'
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedClinicalRecord(
            f'La adenda #{self.pk} es de solo inserción: no se puede borrar.'
        )
