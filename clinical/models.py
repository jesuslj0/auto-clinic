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

La segunda mitad del módulo es la **anamnesis**: cuestionarios versionados
(`QuestionnaireTemplate` → `TemplateVersion` → `Question`) y sus respuestas
(`QuestionnaireResponse`), que congelan una copia literal del cuestionario en el
momento de contestarlo. Misma doctrina, mismos dos niveles (migración 0004).
"""
import copy
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from clinical.conf import retention_years
from clinical.exceptions import (
    EpisodeClosed,
    NoteAlreadySigned,
    ProtectedClinicalRecord,
    TemplateVersionNotPublished,
    TemplateVersionPublished,
)
from clinical.files import (
    attachment_upload_to,
    clinical_media_storage,
    validate_clinical_image,
)
from clinical.hashing import compute_note_hash
from clinical.managers import (
    AppendOnlyInsertManager,
    ClinicalAlertManager,
    LesionManager,
    LesionObservationManager,
    next_history_number,
)
from clinical.snapshots import build_response_snapshot, is_answered
from core.models import SoftDeleteModel, TimeStampedModel


def _frozen_state(instance, fields, loaded_names=None) -> dict:
    """Copia profunda del valor de `fields` en `instance`.

    Se usa para saber, en el `save()`, si alguien tocó un campo congelado. La
    copia es profunda porque los `JSONField` se mutan in situ: sin ella, el
    «valor cargado» y el actual serían el mismo objeto y ningún cambio se vería.

    `loaded_names` limita la captura a los campos que realmente se trajeron de la
    base de datos: con `defer()`/`only()`, leer un campo diferido dispararía una
    consulta por campo solo para vigilarlo.
    """
    if loaded_names is not None:
        fields = [name for name in fields if name in loaded_names]
    return {name: copy.deepcopy(getattr(instance, name)) for name in fields}


def _changed_frozen_fields(instance, loaded: dict) -> list[str]:
    """Campos congelados que difieren de lo que se cargó de la base de datos."""
    return sorted(
        name for name, value in loaded.items() if getattr(instance, name) != value
    )


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


# ---------------------------------------------------------------------------
# Anamnesis: cuestionarios versionados
# ---------------------------------------------------------------------------
#
# Un cuestionario de anamnesis cambia con el tiempo: se añaden preguntas, se
# corrige la redacción de otras, se retira alguna. Pero lo que un paciente
# contestó en 2026 debe poder leerse en 2036 exactamente como se contestó. Se
# resuelve con dos mecanismos que se complementan:
#
#   1. **Versionado.** El cuestionario «lógico» (`QuestionnaireTemplate`) no
#      tiene preguntas; las tienen sus versiones. Publicar una versión la
#      congela: sus preguntas ya no se tocan. Cambiar el cuestionario = publicar
#      una versión nueva, nunca editar la anterior.
#   2. **Snapshot literal.** La respuesta no guarda FK a las preguntas: guarda
#      una copia del texto de cada una junto a lo contestado. Aunque alguien
#      despublique o borre lógicamente la versión, la respuesta sigue siendo
#      legible por sí sola (`clinical/snapshots.py`).
#
# El versionado solo protege del cambio *previsto*; el snapshot protege también
# del imprevisto. Por eso están los dos.


class QuestionnaireTemplate(SoftDeleteModel, TimeStampedModel):
    """Cuestionario de anamnesis como entidad lógica: agrupa sus versiones.

    No contiene preguntas. «El cuestionario de anamnesis podológica» es esto; lo
    que se responde es siempre una `TemplateVersion` concreta.
    """

    clinic = models.ForeignKey(
        'core.Clinic', on_delete=models.CASCADE, related_name='questionnaire_templates'
    )
    name = models.CharField(max_length=150)
    specialty = models.CharField(
        max_length=100, blank=True,
        help_text='Especialidad o tipo de cuestionario, p. ej. «podología general».',
    )
    is_active = models.BooleanField(
        default=True, help_text='Un cuestionario inactivo no se ofrece para rellenar.'
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_questionnaire_template'
        unique_together = ('clinic', 'name')
        ordering = ['name']
        verbose_name = 'cuestionario'
        verbose_name_plural = 'cuestionarios'

    def __str__(self):
        return self.name

    @property
    def current_version(self):
        """Versión vigente, o `None` si aún no se ha publicado ninguna."""
        return self.versions.filter(is_current=True).first()

    def new_draft_version(self, copy_questions_from=None):
        """Crea la siguiente versión en borrador, opcionalmente clonando preguntas.

        Es el camino normal para «editar» un cuestionario ya publicado: se clona
        la versión vigente, se retoca el borrador y se publica. La versión
        anterior no se toca en ningún momento.

        Sin argumento clona la versión vigente (si la hay); con
        `copy_questions_from=False` arranca en blanco.
        """
        source = self.current_version if copy_questions_from is None else copy_questions_from

        with transaction.atomic():
            version = TemplateVersion.objects.create(template=self)
            if source:
                for question in source.questions.all():
                    Question.objects.create(
                        version=version,
                        # El código se clona con la pregunta: es lo único que
                        # mantiene reconocible «la misma pregunta» de una versión
                        # a otra, y de ello vive el motor de alertas.
                        code=question.code,
                        text=question.text,
                        answer_type=question.answer_type,
                        order=question.order,
                        is_required=question.is_required,
                        options=list(question.options or []),
                    )
        return version


class TemplateVersion(SoftDeleteModel, TimeStampedModel):
    """Versión concreta de un cuestionario. Inmutable una vez publicada.

    Publicada, solo admite cambios de *ciclo de vida* (`is_current`,
    `is_published`, borrado lógico): a qué cuestionario pertenece, qué número
    tiene y cuándo se publicó quedan fijos. Sus preguntas, también.

    Despublicar está permitido —es la forma de retirar un cuestionario— y no
    altera ninguna respuesta ya guardada: cada una lleva su propio snapshot.
    """

    # Campos congelados en cuanto la versión está publicada. `is_current`,
    # `is_published` y `deleted_at` quedan fuera a propósito: son estado de ciclo
    # de vida, no contenido del documento.
    FROZEN_FIELDS = ('template_id', 'number', 'published_at')

    template = models.ForeignKey(
        QuestionnaireTemplate, on_delete=models.PROTECT, related_name='versions'
    )
    number = models.PositiveIntegerField(
        help_text='Correlativo dentro del cuestionario; se asigna solo.'
    )
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(
        default=False, help_text='Versión que se sirve al rellenar. Solo una por cuestionario.'
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_template_version'
        unique_together = ('template', 'number')
        ordering = ['template', '-number']
        verbose_name = 'versión de cuestionario'
        verbose_name_plural = 'versiones de cuestionario'
        constraints = [
            # Una sola versión vigente por cuestionario, garantizado por la base
            # de datos y no solo por `make_current()`: dos publicaciones
            # concurrentes no pueden dejar el cuestionario con dos «vigentes».
            models.UniqueConstraint(
                fields=['template'],
                condition=models.Q(is_current=True, deleted_at__isnull=True),
                name='clinical_one_current_version_per_template',
            ),
        ]

    def __str__(self):
        return f'{self.template.name} v{self.number}'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Estado con el que se cargó: la referencia para decidir si esta versión
        # ya estaba publicada ANTES de este save().
        instance._loaded_is_published = instance.is_published
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        return instance

    def _next_number(self) -> int:
        last = (
            TemplateVersion.all_objects.filter(template=self.template)
            .order_by('-number')
            .values_list('number', flat=True)
            .first()
        )
        return (last or 0) + 1

    def save(self, *args, **kwargs):
        if self.number is None and self._state.adding:
            self.number = self._next_number()

        if getattr(self, '_loaded_is_published', False):
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise TemplateVersionPublished(
                    f'La versión {self} está publicada: {", ".join(changed)} no se '
                    f'puede modificar. Publica una versión nueva.'
                )

        super().save(*args, **kwargs)
        self._loaded_is_published = self.is_published
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)

    def publish(self, make_current=True):
        """Publica la versión: la congela y, por defecto, la vuelve vigente."""
        if self.is_published:
            raise TemplateVersionPublished(f'La versión {self} ya está publicada.')
        if not self.questions.exists():
            raise ValidationError(
                f'La versión {self} no tiene preguntas: no se puede publicar.'
            )

        with transaction.atomic():
            self.is_published = True
            self.published_at = timezone.now()
            self.save(update_fields=['is_published', 'published_at', 'updated_at'])
            if make_current:
                self.make_current()

    def make_current(self):
        """Marca esta versión como la vigente y retira la marca a las demás."""
        if not self.is_published:
            raise TemplateVersionNotPublished(
                f'La versión {self} está en borrador: no puede ser la vigente.'
            )

        with transaction.atomic():
            # Primero se degrada la anterior y después se promueve esta, o el
            # índice único de «una sola vigente» saltaría a mitad de camino. Una
            # a una y no con `update()`: los bulk se saltan las señales de audit.
            previous = (
                TemplateVersion.all_objects.filter(template_id=self.template_id, is_current=True)
                .exclude(pk=self.pk)
            )
            for version in previous:
                version.is_current = False
                version.save(update_fields=['is_current', 'updated_at'])

            if not self.is_current:
                self.is_current = True
                self.save(update_fields=['is_current', 'updated_at'])

    def unpublish(self):
        """Retira la versión de circulación. No toca ninguna respuesta guardada."""
        if not self.is_published:
            return
        self.is_published = False
        self.is_current = False
        self.save(update_fields=['is_published', 'is_current', 'updated_at'])


class Question(SoftDeleteModel, TimeStampedModel):
    """Pregunta de una versión concreta. Congelada cuando la versión se publica.

    Pertenece a una versión, nunca al cuestionario: es la pieza que hace que
    «editar una pregunta» sea imposible y haya que publicar una versión nueva.
    """

    class AnswerType(models.TextChoices):
        BOOLEAN = 'boolean', 'Sí / No'
        TEXT = 'text', 'Texto libre'
        SINGLE_CHOICE = 'single_choice', 'Opción única'
        MULTIPLE_CHOICE = 'multiple_choice', 'Opción múltiple'
        NUMBER = 'number', 'Número'

    CHOICE_TYPES = frozenset({AnswerType.SINGLE_CHOICE, AnswerType.MULTIPLE_CHOICE})

    version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name='questions'
    )
    text = models.TextField(help_text='Enunciado literal de la pregunta.')
    code = models.SlugField(
        max_length=60, null=True, blank=True,
        help_text='Identificador estable entre versiones, p. ej. «has_diabetes». '
                  'Es la clave por la que el motor de alertas reconoce la pregunta.',
    )
    answer_type = models.CharField(
        max_length=20, choices=AnswerType.choices, default=AnswerType.TEXT
    )
    order = models.PositiveIntegerField(default=0)
    is_required = models.BooleanField(default=False)
    options = models.JSONField(
        default=list, blank=True,
        help_text='Lista de opciones; solo para los tipos de elección.',
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_question'
        ordering = ['version', 'order', 'id']
        verbose_name = 'pregunta'
        verbose_name_plural = 'preguntas'
        constraints = [
            # Dos preguntas con el mismo código en una versión dejarían al motor
            # de alertas sin saber a cuál hace caso. Las de código nulo no entran
            # (en Postgres los NULL no colisionan), que es lo que permite tener
            # preguntas sin codificar.
            models.UniqueConstraint(
                fields=['version', 'code'],
                condition=models.Q(deleted_at__isnull=True),
                name='clinical_unique_question_code_per_version',
            ),
        ]

    def __str__(self):
        return self.text[:60]

    def _validate_options(self):
        options = self.options or []
        if not isinstance(options, list):
            raise ValidationError({'options': 'Las opciones deben ser una lista.'})
        if self.answer_type in self.CHOICE_TYPES:
            if not options:
                raise ValidationError(
                    {'options': f'Un tipo «{self.get_answer_type_display()}» necesita opciones.'}
                )
        elif options:
            raise ValidationError(
                {'options': f'Un tipo «{self.get_answer_type_display()}» no admite opciones.'}
            )

    def _assert_version_editable(self):
        if self.version_id and self.version.is_published:
            raise TemplateVersionPublished(
                f'La versión {self.version} está publicada: sus preguntas no se '
                f'pueden crear, editar ni borrar. Publica una versión nueva.'
            )

    def clean(self):
        super().clean()
        # Se repite aquí la comprobación del `save()` para que el admin y los
        # formularios la enseñen como error de validación y no como un 500.
        if self.version_id and self.version.is_published:
            raise ValidationError(
                f'La versión {self.version} está publicada y sus preguntas no se '
                f'pueden tocar. Crea una versión nueva.'
            )
        self._validate_options()

    def save(self, *args, **kwargs):
        # Barrera dura, más allá del formulario: una versión publicada no admite
        # preguntas nuevas ni cambios en las que tiene.
        self._assert_version_editable()
        self._validate_options()
        # Cadena vacía y «sin código» son lo mismo, pero solo NULL se libra del
        # índice único: si no se normaliza, dos preguntas sin codificar chocan.
        self.code = self.code or None
        super().save(*args, **kwargs)

    def can_be_deleted(self) -> bool:
        # Ni borrado lógico: una pregunta publicada es parte del documento que
        # los pacientes ya han visto.
        return not (self.version_id and self.version.is_published)


class QuestionnaireResponse(SoftDeleteModel, TimeStampedModel):
    """Cuestionario contestado. Su contenido queda congelado al guardarse.

    El snapshot (`snapshot`) es la fuente de verdad: lleva el texto literal de
    cada pregunta tal y como se le mostró a quien contestó, junto a su respuesta.
    La FK a `TemplateVersion` dice de dónde salió, pero leer la respuesta no
    depende de que esa versión siga existiendo, publicada ni intacta.

    `created_by` es opcional porque hay tres canales de entrada y solo uno tiene
    profesional autenticado: el cuestionario que rellena el paciente desde la web
    o por WhatsApp (vía n8n) no tiene sesión detrás. `source` deja constancia del
    canal, que es justo lo que hay que poder auditar después.
    """

    class Source(models.TextChoices):
        PROFESSIONAL = 'professional', 'Profesional'
        PATIENT_WEB = 'patient_web', 'Paciente (web)'
        PATIENT_WHATSAPP = 'patient_whatsapp', 'Paciente (WhatsApp)'

    # Todo el contenido de la respuesta es inmutable en cuanto existe: no hay
    # borrador que valga. Solo `deleted_at` y `updated_at` pueden cambiar.
    FROZEN_FIELDS = (
        'version_id', 'patient_id', 'episode_id',
        'filled_at', 'source', 'created_by_id', 'snapshot',
    )

    version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name='responses'
    )
    # Mismo patrón que `MedicalHistory`: borrar un paciente no puede arrastrar ni
    # bloquear su documentación clínica (ver el docstring de MedicalHistory).
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.DO_NOTHING,
        related_name='questionnaire_responses', db_constraint=False,
    )
    episode = models.ForeignKey(
        Episode, on_delete=models.PROTECT, related_name='questionnaire_responses'
    )
    filled_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.PROFESSIONAL, db_index=True
    )
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        help_text='Profesional que lo registró; vacío si lo rellenó el paciente.',
    )
    snapshot = models.JSONField(
        default=list,
        help_text='Copia literal de las preguntas y las respuestas dadas.',
    )

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_questionnaire_response'
        ordering = ['-filled_at']
        verbose_name = 'respuesta de cuestionario'
        verbose_name_plural = 'respuestas de cuestionario'

    def __str__(self):
        return f'Respuesta #{self.pk} ({self.get_source_display()})'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        return instance

    @classmethod
    def record(cls, *, version, patient, episode, answers=None,
               source=Source.PROFESSIONAL, created_by=None, filled_at=None,
               derive=True):
        """Registra un cuestionario contestado congelando su snapshot.

        Es la vía normal de alta: `answers` es `{question_id: respuesta}` y se
        convierte en el snapshot en el propio `save()`.

        Al terminar dispara el motor de alertas (`derive=False` lo salta). Es una
        llamada explícita y no una señal a propósito: así el mismo camino sirve
        para el panel, para el formulario del paciente y para la vía del agente
        por n8n, y se ve en el código que dar de alta una anamnesis puede
        levantar avisos en la ficha.
        """
        response = cls(
            version=version, patient=patient, episode=episode,
            source=source, created_by=created_by,
        )
        if filled_at is not None:
            response.filled_at = filled_at
        response.answers = answers or {}
        response.save()

        if derive:
            # Import perezoso: `derivation` importa este módulo.
            from clinical.derivation import derive_alerts

            derive_alerts(response)
        return response

    def clean(self):
        super().clean()
        # Aislamiento por clínica: no se responde el cuestionario de otra clínica.
        if self.version_id and self.patient_id:
            template_clinic_id = self.version.template.clinic_id
            if template_clinic_id != self.patient.clinic_id:
                raise ValidationError(
                    'El cuestionario pertenece a otra clínica distinta a la del paciente.'
                )
        # El episodio tiene que ser del mismo paciente: una anamnesis colgada del
        # episodio de otra persona es un error de historia clínica, no un matiz.
        if self.episode_id and self.patient_id:
            if self.episode.history.patient_id != self.patient_id:
                raise ValidationError(
                    'El episodio pertenece a un paciente distinto al de la respuesta.'
                )

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not self.version.is_published:
                raise TemplateVersionNotPublished(
                    f'La versión {self.version} está en borrador: no se puede '
                    f'responder hasta que se publique.'
                )
            if not self.snapshot:
                # El congelado ocurre aquí y no en la vista: da igual por dónde
                # entre la respuesta (admin, formulario web, n8n), sale con su
                # copia literal hecha.
                self.snapshot = build_response_snapshot(
                    self.version, getattr(self, 'answers', None)
                )
        else:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise ProtectedClinicalRecord(
                    f'La respuesta #{self.pk} está congelada: '
                    f'{", ".join(changed)} no se puede modificar.'
                )

        super().save(*args, **kwargs)
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)

    # --- Lectura del snapshot ------------------------------------------------

    @property
    def questions(self) -> list:
        """Preguntas tal y como se formularon, no las que existen hoy."""
        return list(self.snapshot or [])

    def answer_for(self, question_id):
        """Respuesta dada a una pregunta, o `None` si no está en el snapshot."""
        for entry in self.snapshot or []:
            if entry.get('question_id') == question_id:
                return entry.get('answer')
        return None

    @property
    def is_complete(self) -> bool:
        """`True` si todas las preguntas obligatorias del snapshot tienen respuesta."""
        return all(
            is_answered(entry)
            for entry in (self.snapshot or [])
            if entry.get('required')
        )


# ---------------------------------------------------------------------------
# Alertas clínicas
# ---------------------------------------------------------------------------


class ClinicalAlert(SoftDeleteModel, TimeStampedModel):
    """Aviso permanente sobre un paciente: lo que hay que saber ANTES de tratar.

    Cuelga del paciente y no del episodio a propósito: una diabetes o una alergia
    al látex no caducan al cerrar un proceso asistencial, valen para todos los
    que vengan después.

    Una alerta **no se borra, se desactiva**. `is_active=False` la retira de la
    ficha pero conserva la fila, que es lo que permite responder después a «esto
    se sabía en aquel momento». Por eso `can_be_deleted()` devuelve `False`
    incluso para el borrado lógico: el camino correcto es `deactivate()`.

    El `source` deja preparado el motor de derivación —el que leerá una anamnesis
    y levantará alertas solo— sin construirlo todavía: una alerta derivada
    apuntará a la `QuestionnaireResponse` de la que salió (`source_response`), de
    modo que siempre se pueda contestar de dónde vino cada aviso. Las manuales no
    tienen respuesta de origen.
    """

    class AlertType(models.TextChoices):
        # Las críticas de podología: las que cambian el tratamiento o lo
        # contraindican. `OTHER` existe para no forzar el encaje de lo que no
        # esté en la lista, con el detalle en `note`.
        DIABETES = 'diabetes', 'Diabetes'
        PERIPHERAL_VASCULAR_DISEASE = 'peripheral_vascular_disease', 'Enfermedad vascular periférica'
        NEUROPATHY = 'neuropathy', 'Neuropatía'
        ANTICOAGULANTS = 'anticoagulants', 'Tratamiento anticoagulante'
        ALLERGY_LATEX = 'allergy_latex', 'Alergia al látex'
        ALLERGY_LOCAL_ANESTHETICS = 'allergy_local_anesthetics', 'Alergia a anestésicos locales'
        OTHER = 'other', 'Otra'

    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Crítica'
        WARNING = 'warning', 'Advertencia'
        INFO = 'info', 'Informativa'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        DERIVED = 'derived', 'Derivada de una anamnesis'

    # Mismo patrón que `MedicalHistory`: borrar un paciente no puede arrastrar ni
    # bloquear su documentación clínica (ver el docstring de MedicalHistory).
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.DO_NOTHING,
        related_name='clinical_alerts', db_constraint=False,
    )
    alert_type = models.CharField(max_length=40, choices=AlertType.choices, db_index=True)
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.WARNING, db_index=True
    )
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL, db_index=True
    )
    note = models.TextField(blank=True, help_text='Detalle del aviso.')
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        help_text='Profesional que la dio de alta; vacío si la levantó el sistema.',
    )
    # PROTECT y no SET_NULL: una respuesta de cuestionario no se borra nunca
    # físicamente, así que la procedencia de una alerta derivada no puede
    # evaporarse.
    source_response = models.ForeignKey(
        QuestionnaireResponse, on_delete=models.PROTECT,
        null=True, blank=True, related_name='derived_alerts',
        help_text='Respuesta de anamnesis de la que se derivó. Vacío si es manual.',
    )

    objects = ClinicalAlertManager()

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_alert'
        ordering = ['-created_at']
        verbose_name = 'alerta clínica'
        verbose_name_plural = 'alertas clínicas'
        indexes = [
            # La consulta de la ficha: alertas vigentes de un paciente.
            models.Index(
                fields=['patient', 'is_active', 'severity'],
                name='idx_clinical_alert_patient',
            ),
        ]
        constraints = [
            # La idempotencia del motor de derivación, respaldada por la base de
            # datos: una alerta derivada queda identificada por paciente, tipo y
            # respuesta de origen. Dos derivaciones simultáneas de la misma
            # respuesta no pueden dejar dos avisos iguales. No afecta a las
            # manuales, que quedan fuera de la condición.
            models.UniqueConstraint(
                fields=['patient', 'alert_type', 'source_response'],
                condition=models.Q(source='derived', deleted_at__isnull=True),
                name='clinical_unique_derived_alert',
            ),
        ]

    def __str__(self):
        return f'{self.get_alert_type_display()} ({self.get_severity_display()})'

    def _validate_source(self):
        """La procedencia tiene que cuadrar con el origen declarado."""
        if self.source == self.Source.DERIVED and self.source_response_id is None:
            raise ValidationError(
                {'source_response': 'Una alerta derivada debe apuntar a la respuesta de origen.'}
            )
        if self.source == self.Source.MANUAL and self.source_response_id is not None:
            raise ValidationError(
                {'source_response': 'Una alerta manual no procede de ninguna respuesta.'}
            )

    def clean(self):
        super().clean()
        self._validate_source()

    def save(self, *args, **kwargs):
        # Barrera más allá del formulario: que nadie deje una alerta «derivada»
        # sin decir de dónde salió, que es justo lo que el motor necesitará.
        self._validate_source()
        super().save(*args, **kwargs)

    def deactivate(self):
        """Retira la alerta de la ficha conservando la fila. Idempotente."""
        if not self.is_active:
            return
        self.is_active = False
        self.save(update_fields=['is_active', 'updated_at'])

    def reactivate(self):
        """Vuelve a poner la alerta en circulación. Idempotente."""
        if self.is_active:
            return
        self.is_active = True
        self.save(update_fields=['is_active', 'updated_at'])

    def can_be_deleted(self) -> bool:
        # Una alerta no se borra: se desactiva. Conservar la fila es lo que
        # permite reconstruir qué se sabía del paciente en cada momento.
        return False

    def delete(self, using=None, keep_parents=False):
        raise ProtectedClinicalRecord(
            f'La alerta #{self.pk} no se borra: se desactiva con deactivate().'
        )


# ---------------------------------------------------------------------------
# Lesiones sobre el mapa del pie
# ---------------------------------------------------------------------------


class Lesion(SoftDeleteModel, TimeStampedModel):
    """Lesión localizada sobre el mapa del pie.

    Guarda la localización **dos veces y a propósito**, porque son dos cosas
    distintas que envejecen distinto:

    - `anatomical_zone` es el dato **clínico**: «cabeza del primer metatarsiano»
      significa lo mismo hoy que dentro de quince años y es lo que se consulta,
      se agrega y se lee en un informe.
    - `x`/`y` son solo **para pintar**: fracciones (0–1) de las dimensiones del
      SVG, nunca píxeles. Normalizadas porque el dibujo se reescala en cada
      pantalla, y un número de píxel no significaría nada fuera del tamaño en el
      que se marcó.

    Si mañana se rediseña el SVG, las coordenadas viejas dejan de cuadrar, pero
    **la zona anatómica sigue siendo válida**: por eso el dato clínico no depende
    del dibujo. Nunca se debe derivar la zona de las coordenadas ni al revés.

    La localización queda **fija al crearse**. Una lesión no «se mueve»: si la
    marca estaba mal, se borra lógicamente y se registra otra; si lo que cambia
    es la evolución, eso es el ciclo de vida (`resolve()`), no la posición.
    """

    class Laterality(models.TextChoices):
        LEFT = 'left', 'Pie izquierdo'
        RIGHT = 'right', 'Pie derecho'

    class View(models.TextChoices):
        DORSAL = 'dorsal', 'Dorsal'
        PLANTAR = 'plantar', 'Plantar'
        MEDIAL = 'medial', 'Medial'
        LATERAL = 'lateral', 'Lateral'

    class AnatomicalZone(models.TextChoices):
        """Zonas codificadas. Slugs estables: sobreviven a un rediseño del SVG.

        NO es texto libre a propósito. Una zona escrita a mano («1er meta»,
        «primer metatarsiano», «MTT1») no se puede consultar ni agregar, y es
        justo lo que hay que poder hacer con una lesión: buscarla, contarla y
        compararla entre visitas.
        """

        HALLUX = 'hallux', 'Hallux (primer dedo)'
        SECOND_TOE = 'second_toe', 'Segundo dedo'
        THIRD_TOE = 'third_toe', 'Tercer dedo'
        FOURTH_TOE = 'fourth_toe', 'Cuarto dedo'
        FIFTH_TOE = 'fifth_toe', 'Quinto dedo'
        FIRST_METATARSAL = 'first_metatarsal', 'Primer metatarsiano'
        SECOND_METATARSAL = 'second_metatarsal', 'Segundo metatarsiano'
        THIRD_METATARSAL = 'third_metatarsal', 'Tercer metatarsiano'
        FOURTH_METATARSAL = 'fourth_metatarsal', 'Cuarto metatarsiano'
        FIFTH_METATARSAL = 'fifth_metatarsal', 'Quinto metatarsiano'
        INTERDIGITAL = 'interdigital', 'Espacio interdigital'
        MIDFOOT = 'midfoot', 'Mediopié'
        MEDIAL_ARCH = 'medial_arch', 'Arco medial'
        LATERAL_BORDER = 'lateral_border', 'Borde lateral'
        HEEL = 'heel', 'Talón'
        ANKLE = 'ankle', 'Tobillo'
        OTHER = 'other', 'Otra zona'

    class LesionType(models.TextChoices):
        ULCER = 'ulcer', 'Úlcera'
        HYPERKERATOSIS = 'hyperkeratosis', 'Hiperqueratosis'
        WOUND = 'wound', 'Herida'
        BLISTER = 'blister', 'Ampolla'
        OTHER = 'other', 'Otra'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activa'
        RESOLVED = 'resolved', 'Resuelta'

    # Dónde está la lesión: fijo desde que se crea.
    FROZEN_FIELDS = (
        'episode_id', 'laterality', 'view', 'anatomical_zone', 'x', 'y',
    )

    episode = models.ForeignKey(Episode, on_delete=models.PROTECT, related_name='lesions')
    laterality = models.CharField(max_length=5, choices=Laterality.choices, db_index=True)
    view = models.CharField(max_length=10, choices=View.choices, db_index=True)
    anatomical_zone = models.CharField(
        max_length=30, choices=AnatomicalZone.choices, db_index=True,
        help_text='Zona clínica. Es el dato que sobrevive a un rediseño del mapa.',
    )
    x = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Fracción horizontal del SVG (0–1), nunca píxeles.',
    )
    y = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Fracción vertical del SVG (0–1), nunca píxeles.',
    )
    lesion_type = models.CharField(
        max_length=20, choices=LesionType.choices, default=LesionType.OTHER, db_index=True
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    detected_at = models.DateField(default=timezone.localdate)
    resolved_at = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    objects = LesionManager()

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_lesion'
        ordering = ['-detected_at', '-id']
        verbose_name = 'lesión'
        verbose_name_plural = 'lesiones'
        indexes = [
            # La consulta del mapa: las lesiones de una vista concreta del pie.
            models.Index(
                fields=['episode', 'laterality', 'view'],
                name='idx_lesion_episode_view',
            ),
        ]
        constraints = [
            # Segundo nivel, como el resto de la capa: los validadores del campo
            # solo corren en `full_clean()`, y esto no admite excepciones ni
            # aunque alguien entre por SQL.
            models.CheckConstraint(
                condition=(
                    models.Q(x__gte=0.0) & models.Q(x__lte=1.0)
                    & models.Q(y__gte=0.0) & models.Q(y__lte=1.0)
                ),
                name='clinical_lesion_coordinates_normalized',
            ),
            # Resuelta ⇒ con fecha de resolución; activa ⇒ sin ella. Una lesión
            # «resuelta» sin fecha no se puede situar en el tiempo, y una activa
            # con fecha de alta es una contradicción.
            models.CheckConstraint(
                condition=(
                    models.Q(status='resolved', resolved_at__isnull=False)
                    | models.Q(status='active', resolved_at__isnull=True)
                ),
                name='clinical_lesion_resolved_at_matches_status',
            ),
        ]

    def __str__(self):
        return (
            f'{self.get_lesion_type_display()} en {self.get_anatomical_zone_display()} '
            f'({self.get_laterality_display()})'
        )

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        return instance

    def _validate_coordinates(self):
        for name in ('x', 'y'):
            value = getattr(self, name)
            if value is None or not (0.0 <= value <= 1.0):
                raise ValidationError({
                    name: 'Las coordenadas son fracciones del SVG entre 0.0 y 1.0, no píxeles.'
                })

    def _validate_status(self):
        if self.status == self.Status.RESOLVED and self.resolved_at is None:
            raise ValidationError({
                'resolved_at': 'Una lesión resuelta necesita fecha de resolución.'
            })
        if self.status == self.Status.ACTIVE and self.resolved_at is not None:
            raise ValidationError({
                'resolved_at': 'Una lesión activa no puede tener fecha de resolución.'
            })

    def clean(self):
        super().clean()
        self._validate_coordinates()
        self._validate_status()

    def save(self, *args, **kwargs):
        # Barreras duras, más allá del formulario: `clean()` solo lo llama quien
        # se acuerda, y estas dos reglas no pueden depender de eso.
        self._validate_coordinates()
        self._validate_status()

        if not self._state.adding:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise ProtectedClinicalRecord(
                    f'La lesión #{self.pk} no se puede recolocar: '
                    f'{", ".join(changed)} queda fijo desde que se registra.'
                )

        super().save(*args, **kwargs)
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)

    def evolution(self):
        """Observaciones de la lesión en orden cronológico, de la primera a la última.

        Es como se lee un seguimiento; el `ordering` del modelo, al revés, es
        como se lee una ficha. Vive aquí para que ninguna vista tenga que
        acordarse de invertirlo.
        """
        return self.observations.all().chronological()

    def resolve(self, on=None):
        """Marca la lesión como resuelta. `on` por defecto, hoy."""
        if self.status == self.Status.RESOLVED:
            return
        self.status = self.Status.RESOLVED
        self.resolved_at = on or timezone.localdate()
        self.save(update_fields=['status', 'resolved_at', 'updated_at'])

    def reopen(self):
        """Vuelve a poner la lesión en activa y retira la fecha de resolución."""
        if self.status == self.Status.ACTIVE:
            return
        self.status = self.Status.ACTIVE
        self.resolved_at = None
        self.save(update_fields=['status', 'resolved_at', 'updated_at'])


# ---------------------------------------------------------------------------
# Seguimiento de la lesión: observaciones y fotos
# ---------------------------------------------------------------------------
#
# Una lesión no es un dato, es una serie. Lo que interesa clínicamente no es
# «hay una úlcera en el primer metatarsiano» sino si mide menos que hace tres
# semanas. Por eso la lesión guarda su localización (fija) y cada visita añade
# una `LesionObservation` con las medidas de ese día.
#
# Las fotos cuelgan de la observación y no de la lesión: una foto sin la fecha y
# las medidas de ese momento no dice nada, y colgándola de la observación queda
# atada a la visita en la que se tomó.


class LesionObservation(SoftDeleteModel, TimeStampedModel):
    """Cómo estaba la lesión en una visita concreta.

    Es la unidad de la evolución: la lesión no cambia de sitio ni de identidad,
    lo que cambia es lo que se ve de ella cada día. Por eso las medidas están
    aquí y no en `Lesion`.

    Las medidas van en **campos separados y numéricos** (largo, ancho, profundo,
    en milímetros) y no en un texto ni en un JSON suelto: el sentido de medir una
    úlcera es poder compararla con la de la semana pasada, y eso exige un número
    consultable. Son opcionales porque no toda lesión se mide (una hiperqueratosis
    se describe), pero cuando se miden, se miden igual siempre.

    `lesion` y `visit` quedan **fijos**: una observación es de esa lesión en esa
    visita. Si se anotó donde no era, se borra lógicamente y se registra otra.
    """

    # De qué lesión y de qué encuentro es esta observación: no se reasigna.
    FROZEN_FIELDS = ('lesion_id', 'visit_id')

    lesion = models.ForeignKey(Lesion, on_delete=models.PROTECT, related_name='observations')
    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='lesion_observations'
    )
    observed_at = models.DateField(
        default=timezone.localdate,
        help_text='Día de la observación. Es el eje de la evolución.',
    )
    length_mm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Largo en milímetros.',
    )
    width_mm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Ancho en milímetros.',
    )
    depth_mm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Profundidad en milímetros.',
    )
    description = models.TextField(blank=True, help_text='Descripción clínica del estado.')
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    objects = LesionObservationManager()

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_lesion_observation'
        # La más reciente primero, que es como se lee una ficha. La serie en
        # orden cronológico la da `Lesion.evolution()`.
        ordering = ['-observed_at', '-id']
        verbose_name = 'observación de lesión'
        verbose_name_plural = 'observaciones de lesión'
        indexes = [
            models.Index(fields=['lesion', 'observed_at'], name='idx_lesion_obs_series'),
        ]
        constraints = [
            # Segundo nivel: los validadores solo corren en `full_clean()`. Una
            # medida negativa no es un matiz de formulario, es un dato imposible.
            models.CheckConstraint(
                condition=(
                    (models.Q(length_mm__isnull=True) | models.Q(length_mm__gte=0))
                    & (models.Q(width_mm__isnull=True) | models.Q(width_mm__gte=0))
                    & (models.Q(depth_mm__isnull=True) | models.Q(depth_mm__gte=0))
                ),
                name='clinical_lesion_observation_measurements_positive',
            ),
        ]

    def __str__(self):
        return f'Observación de lesión #{self.lesion_id} ({self.observed_at:%Y-%m-%d})'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        return instance

    def _validate_visit(self):
        """La visita y la lesión tienen que ser del mismo episodio.

        Observar la lesión de un episodio dentro de la visita de otro no es un
        despiste de formulario: deja la evolución de una lesión repartida entre
        procesos asistenciales distintos y la vuelve ilegible.
        """
        if self.lesion_id and self.visit_id:
            if self.lesion.episode_id != self.visit.episode_id:
                raise ValidationError({
                    'visit': 'La visita pertenece a un episodio distinto al de la lesión.'
                })

    def clean(self):
        super().clean()
        self._validate_visit()

    def save(self, *args, **kwargs):
        # Barrera dura, más allá del formulario.
        self._validate_visit()

        if not self._state.adding:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise ProtectedClinicalRecord(
                    f'La observación #{self.pk} no se puede reasignar: '
                    f'{", ".join(changed)} queda fijo desde que se registra.'
                )

        super().save(*args, **kwargs)
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)

    def delete(self, using=None, keep_parents=False):
        # Django no cascadea el borrado lógico: los adjuntos se recorren a mano.
        # El objeto del bucket NO se toca (ver `LesionAttachment.delete`).
        for attachment in LesionAttachment.all_objects.filter(
            observation=self, deleted_at__isnull=True
        ):
            attachment.delete()
        super().delete(using=using, keep_parents=keep_parents)


class LesionAttachment(SoftDeleteModel, TimeStampedModel):
    """Foto clínica de una observación, guardada en un bucket privado.

    Tres reglas que no se negocian:

    - **Se guarda la clave, nunca una URL.** Una URL firmada caduca en minutos;
      persistirla sería guardar una credencial caducada y, peor, un enlace de
      acceso a dato de salud dentro de la base de datos. La URL se genera en cada
      petición y solo después de comprobar permisos (`clinical/attachments.py`).
    - **La clave es un UUID.** Nada de `paciente_perez_pie_izq.jpg`: el nombre de
      un objeto es visible para cualquiera que vea la clave y no puede contar qué
      le pasa a quién (`clinical.files.attachment_upload_to`).
    - **El fichero se valida por su contenido**, no por su extensión ni por el
      `Content-Type` que mande el cliente, y el resultado de esa validación es lo
      que se guarda en `mime_type`/`size_bytes`/`checksum`. Ocurre en `save()`, no
      en un formulario, para que valga igual venga de la consulta o de WhatsApp.

    Una vez subido queda **congelado**: ni se reemplaza el fichero ni se retoca
    su procedencia. Corregir es borrar lógicamente y subir otro.
    """

    class Source(models.TextChoices):
        PROFESSIONAL = 'professional', 'Profesional'
        PATIENT_WHATSAPP = 'patient_whatsapp', 'Paciente (WhatsApp)'
        PATIENT_WEB = 'patient_web', 'Paciente (web)'

    #: Orígenes que NO son la consulta: se validan con el límite estricto.
    EXTERNAL_SOURCES = frozenset({Source.PATIENT_WHATSAPP, Source.PATIENT_WEB})

    # Todo lo que describe el fichero queda fijo tras la subida. `deleted_at` y
    # `updated_at` quedan fuera: son ciclo de vida.
    FROZEN_FIELDS = ('observation_id', 'mime_type', 'size_bytes', 'checksum', 'source')

    # Identificador público: es el que viaja en la URL de descarga. La PK
    # secuencial se queda dentro, para que un id ajeno no se pueda ni tantear.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    observation = models.ForeignKey(
        LesionObservation, on_delete=models.PROTECT, related_name='attachments'
    )
    file = models.FileField(
        upload_to=attachment_upload_to,
        storage=clinical_media_storage,
        max_length=255,
        help_text='Clave del objeto en el bucket privado. Nunca una URL.',
    )
    mime_type = models.CharField(
        max_length=100, blank=True,
        help_text='Tipo real del contenido, deducido al validar. No lo elige quien sube.',
    )
    size_bytes = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(
        max_length=71, blank=True,
        help_text='sha256:<hexdigest> del contenido tal y como se subió.',
    )
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.PROFESSIONAL, db_index=True
    )
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'clinical_lesion_attachment'
        ordering = ['-uploaded_at', '-id']
        verbose_name = 'adjunto de lesión'
        verbose_name_plural = 'adjuntos de lesión'

    def __str__(self):
        return f'Adjunto #{self.pk} de observación #{self.observation_id}'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        # El fichero aparte: comparar `FieldFile`s copiaría el backend entero.
        # Lo que identifica al fichero es su clave.
        instance._loaded_file_name = instance.file.name
        return instance

    @property
    def is_external(self) -> bool:
        """`True` si no lo subió un profesional desde la consulta."""
        return self.source in self.EXTERNAL_SOURCES

    @property
    def patient(self):
        """Paciente al que pertenece la foto, subiendo por la cadena clínica."""
        return self.observation.lesion.episode.history.patient

    def clean(self):
        super().clean()
        # En el admin y en los formularios, el motivo del rechazo se enseña como
        # error de campo en vez de reventar en el `save()`.
        if self._state.adding and self.file:
            try:
                validate_clinical_image(self.file, external=self.is_external)
            except ValidationError as exc:
                raise ValidationError({'file': exc.messages})

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not self.file:
                raise ValidationError({'file': 'El adjunto necesita un fichero.'})
            # La validación vive aquí y no en un formulario a propósito: es la
            # única forma de que el adjunto que entre por la vía del agente pase
            # exactamente por el mismo filtro que el que sube el profesional.
            probe = validate_clinical_image(self.file, external=self.is_external)
            # Se guarda lo que el fichero ES, no lo que dijo quien lo subió. El
            # `mime_type` ya está puesto cuando `attachment_upload_to` calcula la
            # clave, así que la extensión sale también del contenido real.
            self.mime_type = probe.mime_type
            self.size_bytes = probe.size_bytes
            self.checksum = probe.checksum
        else:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if self.file.name != getattr(self, '_loaded_file_name', self.file.name):
                changed = sorted(changed + ['file'])
            if changed:
                raise ProtectedClinicalRecord(
                    f'El adjunto #{self.pk} está congelado: '
                    f'{", ".join(changed)} no se puede modificar. '
                    f'Para corregirlo, bórralo y sube otro.'
                )

        super().save(*args, **kwargs)
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)
        self._loaded_file_name = self.file.name

    def delete(self, using=None, keep_parents=False):
        """Borrado lógico. **El objeto del bucket se conserva.**

        Es intencionado y del mismo signo que el resto de la capa: la fila
        desaparece de la ficha, pero el fichero sigue ahí mientras corra el plazo
        de conservación. Borrarlo del bucket sería el único borrado físico e
        irreversible de toda la capa clínica, y no se hace desde una vista.
        """
        super().delete(using=using, keep_parents=keep_parents)
