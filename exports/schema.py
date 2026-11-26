"""Qué se exporta, de qué modelo y con qué campos.

Es el único sitio donde se decide el contenido de una exportación. Cada entidad
declara de forma **explícita** los campos que salen y, aparte, los que se dejan
fuera con su motivo. Un test (`tests/exports/test_schema_guard.py`) exige que
`fields + clinic_only + excluded` sean exactamente los campos del modelo: quien
añada un campo al modelo está obligado a decidir aquí si sale.

Reglas que todo lo de este módulo respeta:

- **Se lee todo, también lo borrado.** Los modelos con borrado lógico se leen por
  `all_objects` y llevan `deleted_at`. Los `objects` de `Lesion`,
  `LesionObservation`, `ClinicalAlert`, `PatientInvoice` y `Payment` ocultan
  borrados y no se usan nunca.
- **Se filtra por `patient_id` / `clinic_id`, no recorriendo `patient.<relación>`.**
  La FK a `Patient` de la capa clínica y de facturación es `DO_NOTHING` sin
  constraint: puede apuntar a un paciente que ya no existe.
- **Se exporta lo congelado**, tal cual está guardado (`frozen_*`, `text_copy`,
  `snapshot`, `lines`). Nada se recalcula desde el catálogo vigente.
- **Los secretos nunca salen**: claves de API, tokens, hashes de contraseña,
  secretos TOTP, tokens de cita y de enlace de ficha.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.db.models import Q, QuerySet

from agent.models import (
    AgentProfile,
    ChatAttachment,
    ChatMessage,
    ConversationSession,
    WhatsAppTemplate,
)
from appointments.models import (
    Appointment,
    AppointmentStatusHistory,
    Professional,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)
from audit.models import AccessLog, ChangeLog
from billing.models import InvoiceSequence, PatientInvoice, Payment, ReceiptSequence
from clinical.models import (
    Addendum,
    ClinicalAlert,
    ClinicalNote,
    ConsentTemplate,
    ConsentVersion,
    Episode,
    HistorySequence,
    Lesion,
    LesionAttachment,
    LesionObservation,
    MedicalHistory,
    PatientFormLink,
    PerformedProcedure,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    SignedConsent,
    TemplateVersion,
    Visit,
)
from core.models import Clinic, User
from knowledge.models import ClinicKnowledgeBase
from patients.models import Guardian, Patient, PatientGuardian
from services.models import Service, ServiceCategory

SCHEMA_VERSION = '1'

PATIENT = 'patient'
CLINIC = 'clinic'


@dataclass(frozen=True)
class Scope:
    """Qué se exporta: un paciente de una clínica, o la clínica entera."""

    clinic: Clinic
    patient: Patient | None = None

    @property
    def kind(self) -> str:
        return PATIENT if self.patient is not None else CLINIC


@dataclass(frozen=True)
class FileSpec:
    """Un fichero del bucket (o del storage público) colgado de una fila."""

    #: Nombre del `FileField` del modelo.
    field: str
    #: Carpeta bajo `adjuntos/` en el ZIP.
    folder: str
    #: Campo con el `sha256:<hex>` guardado al subirlo, si existe.
    checksum_field: str | None = None


@dataclass(frozen=True)
class Entity:
    name: str
    model: type
    #: Campos (atributos, `patient_id` y no `patient`) que salen en cualquier ámbito.
    fields: tuple[str, ...]
    #: Cómo se obtienen las filas del ámbito pedido.
    queryset: Callable[[Scope], QuerySet]
    #: Campos que salen solo en la exportación de la clínica.
    clinic_only: tuple[str, ...] = ()
    #: Campos que no salen nunca, con el motivo. Cuenta para el test guardián.
    excluded: dict[str, str] = field(default_factory=dict)
    #: Campos de modelos relacionados (`user__first_name`); no cuentan para el guardián.
    lookups: tuple[str, ...] = ()
    #: Ámbitos en los que la entidad existe.
    scopes: tuple[str, ...] = (PATIENT, CLINIC)
    file: FileSpec | None = None
    pk_field: str = 'id'

    def fields_for(self, scope: Scope) -> tuple[str, ...]:
        if scope.kind == CLINIC:
            return self.fields + self.clinic_only + self.lookups
        return self.fields + self.lookups

    def concrete_attnames(self) -> set[str]:
        return {f.attname for f in self.model._meta.concrete_fields}

    def accounted_for(self) -> set[str]:
        return set(self.fields) | set(self.clinic_only) | set(self.excluded)


def all_rows(model) -> QuerySet:
    """Todas las filas del modelo, también las borradas lógicamente."""
    manager = getattr(model, 'all_objects', None) or model._base_manager
    return manager.all()


# ---------------------------------------------------------------------------
# Filtros por ámbito
# ---------------------------------------------------------------------------

def _by_history(scope: Scope, prefix: str) -> dict:
    """Filtro por la cadena `…history` de la capa clínica.

    `prefix` es el camino hasta `MedicalHistory` (`history`, `episode__history`…).
    Una sola historia por paciente y por clínica: no se pasa por `Patient`.
    """
    if scope.patient is not None:
        return {f'{prefix}__patient_id': scope.patient.pk}
    return {f'{prefix}__clinic_id': scope.clinic.pk}


def _by_patient_or_clinic(scope: Scope, patient_lookup: str = 'patient_id',
                          clinic_lookup: str = 'clinic_id') -> dict:
    if scope.patient is not None:
        return {patient_lookup: scope.patient.pk}
    return {clinic_lookup: scope.clinic.pk}


def _patients_of_clinic(clinic: Clinic) -> Q:
    """Pacientes de la clínica, incluidos los que solo dejaron historia.

    Un paciente borrado deja filas huérfanas que ya no se alcanzan por
    `Patient.clinic`; su `MedicalHistory` (nunca se borra) sí sigue diciendo de
    qué clínica era.
    """
    return Q(patient_id__in=Patient.objects.filter(clinic_id=clinic.pk).values('pk')) | Q(
        patient_id__in=all_rows(MedicalHistory).filter(clinic_id=clinic.pk).values('patient_id')
    )


# ---------------------------------------------------------------------------
# Querysets
# ---------------------------------------------------------------------------

def _patients(scope):
    if scope.patient is not None:
        return Patient.objects.filter(pk=scope.patient.pk)
    return Patient.objects.filter(clinic_id=scope.clinic.pk)


def _guardians(scope):
    if scope.patient is not None:
        links = all_rows(PatientGuardian).filter(patient_id=scope.patient.pk)
        return Guardian.objects.filter(pk__in=links.values('guardian_id'))
    return Guardian.objects.filter(clinic_id=scope.clinic.pk)


def _patient_guardians(scope):
    if scope.patient is not None:
        return all_rows(PatientGuardian).filter(patient_id=scope.patient.pk)
    # Por el contacto, que sí cuelga de la clínica: el paciente puede no existir ya.
    return all_rows(PatientGuardian).filter(guardian__clinic_id=scope.clinic.pk)


def _professionals(scope):
    if scope.patient is not None:
        # Solo quienes aparecen en la historia de este paciente: su copia no tiene
        # por qué listar a todo el equipo de la clínica.
        history = {'episode__history__patient_id': scope.patient.pk}
        in_note = {'visit__episode__history__patient_id': scope.patient.pk}
        in_addendum = {'note__visit__episode__history__patient_id': scope.patient.pk}
        return Professional.objects.filter(
            Q(pk__in=all_rows(Visit).filter(**history).values('professional_id'))
            | Q(pk__in=all_rows(ClinicalNote).filter(**in_note).values('signed_by_id'))
            | Q(pk__in=Addendum.objects.filter(**in_addendum).values('author_id'))
            | Q(pk__in=all_rows(Episode).filter(history__patient_id=scope.patient.pk)
                .values('responsible_professional_id'))
        )
    return Professional.objects.filter(clinic_id=scope.clinic.pk)


def _medical_histories(scope):
    return all_rows(MedicalHistory).filter(**_by_patient_or_clinic(scope))


def _episodes(scope):
    return all_rows(Episode).filter(**_by_history(scope, 'history'))


def _visits(scope):
    return all_rows(Visit).filter(**_by_history(scope, 'episode__history'))


def _notes(scope):
    return all_rows(ClinicalNote).filter(**_by_history(scope, 'visit__episode__history'))


def _addenda(scope):
    return all_rows(Addendum).filter(**_by_history(scope, 'note__visit__episode__history'))


def _responses(scope):
    return all_rows(QuestionnaireResponse).filter(**_by_history(scope, 'episode__history'))


def _alerts(scope):
    if scope.patient is not None:
        return all_rows(ClinicalAlert).filter(patient_id=scope.patient.pk)
    return all_rows(ClinicalAlert).filter(_patients_of_clinic(scope.clinic))


def _lesions(scope):
    return all_rows(Lesion).filter(**_by_history(scope, 'episode__history'))


def _observations(scope):
    return all_rows(LesionObservation).filter(**_by_history(scope, 'lesion__episode__history'))


def _lesion_attachments(scope):
    return all_rows(LesionAttachment).filter(
        **_by_history(scope, 'observation__lesion__episode__history')
    )


def _procedures(scope):
    return all_rows(PerformedProcedure).filter(**_by_history(scope, 'visit__episode__history'))


def _signed_consents(scope):
    return all_rows(SignedConsent).filter(**_by_history(scope, 'episode__history'))


def _form_links(scope):
    return PatientFormLink.objects.filter(**_by_patient_or_clinic(scope))


def _appointments(scope):
    return Appointment.objects.filter(**_by_patient_or_clinic(scope))


def _appointment_history(scope):
    if scope.patient is not None:
        return AppointmentStatusHistory.objects.filter(appointment__patient_id=scope.patient.pk)
    return AppointmentStatusHistory.objects.filter(appointment__clinic_id=scope.clinic.pk)


def _invoices(scope):
    return all_rows(PatientInvoice).filter(**_by_patient_or_clinic(scope))


def _payments(scope):
    if scope.patient is not None:
        return all_rows(Payment).filter(invoice__patient_id=scope.patient.pk)
    return all_rows(Payment).filter(clinic_id=scope.clinic.pk)


def _conversations(scope):
    return ConversationSession.objects.filter(
        is_test=False, **_by_patient_or_clinic(scope)
    )


def _messages(scope):
    if scope.patient is not None:
        return ChatMessage.objects.filter(
            session__patient_id=scope.patient.pk, session__is_test=False
        )
    return ChatMessage.objects.filter(clinic_id=scope.clinic.pk, session__is_test=False)


def _chat_attachments(scope):
    if scope.patient is not None:
        return ChatAttachment.objects.filter(
            message__session__patient_id=scope.patient.pk, message__session__is_test=False
        )
    return ChatAttachment.objects.filter(
        message__clinic_id=scope.clinic.pk, message__session__is_test=False
    )


# Solo ámbito clínica -------------------------------------------------------

def _clinic(scope):
    return Clinic.objects.filter(pk=scope.clinic.pk)


def _by_clinic(model):
    def queryset(scope):
        return all_rows(model).filter(clinic_id=scope.clinic.pk)
    return queryset


def _via(model, lookup):
    def queryset(scope):
        return all_rows(model).filter(**{lookup: scope.clinic.pk})
    return queryset


def _audit_rows(model):
    """Rastro de auditoría de la clínica: lo que hizo su personal y lo que tocó a sus pacientes.

    Los registros no llevan clínica; se acotan por el usuario y por el paciente
    (incluidos los de pacientes que ya no existen, a través de su historia).
    """
    def queryset(scope):
        return all_rows(model).filter(
            Q(user__clinic_id=scope.clinic.pk) | _patients_of_clinic(scope.clinic)
        )
    return queryset


def _professional_services(scope):
    through = Professional.services.through
    return through.objects.filter(professional__clinic_id=scope.clinic.pk)


def _professional_schedules(scope):
    return ProfessionalSchedule.objects.filter(professional__clinic_id=scope.clinic.pk)


def _professional_time_off(scope):
    return ProfessionalTimeOff.objects.filter(professional__clinic_id=scope.clinic.pk)


# ---------------------------------------------------------------------------
# Entidades
# ---------------------------------------------------------------------------

def _timestamps(*extra):
    return ('created_at', 'updated_at') + extra


ENTITIES: tuple[Entity, ...] = (
    Entity(
        name='patients',
        model=Patient,
        fields=(
            'id', 'first_name', 'last_name', 'email', 'phone', 'date_of_birth', 'tax_id',
            'address', 'postal_code', 'city', 'notes', 'photo', 'archived_at',
            *_timestamps(),
        ),
        clinic_only=('clinic_id', 'archived_by_id'),
        queryset=_patients,
        file=FileSpec('photo', 'patient-photos'),
    ),
    Entity(
        name='guardians',
        model=Guardian,
        # En la copia de un paciente, de su contacto responsable solo se da lo
        # mínimo (quién es); teléfono, correo y notas son de un tercero.
        fields=('id', 'first_name', 'last_name'),
        clinic_only=('clinic_id', 'phone', 'email', 'notes', 'patient_id', *_timestamps()),
        excluded={},
        queryset=_guardians,
    ),
    Entity(
        name='patient_guardians',
        model=PatientGuardian,
        fields=('id', 'patient_id', 'guardian_id', 'relationship', 'deleted_at', *_timestamps()),
        clinic_only=('created_by_id',),
        queryset=_patient_guardians,
    ),
    Entity(
        name='professionals',
        model=Professional,
        # Con el paciente solo sale quién firmó o atendió y con qué colegiación;
        # contacto, biografía y horarios son de la clínica.
        fields=('id', 'professional_type', 'title', 'license_number', 'license_body'),
        clinic_only=(
            'user_id', 'clinic_id', 'photo', 'bio', 'contact_phone', 'specialties', 'education',
            'years_experience', 'languages', 'is_active', 'accepts_online_booking',
            'buffer_minutes', 'slot_granularity_minutes',
        ),
        lookups=('user__first_name', 'user__last_name'),
        queryset=_professionals,
        file=FileSpec('photo', 'professional-photos'),
    ),
    Entity(
        name='medical_histories',
        model=MedicalHistory,
        fields=('id', 'patient_id', 'number', 'opened_at', 'deleted_at', *_timestamps()),
        clinic_only=('clinic_id',),
        queryset=_medical_histories,
    ),
    Entity(
        name='episodes',
        model=Episode,
        fields=(
            'id', 'history_id', 'reason', 'opened_at', 'discharged_at', 'status',
            'responsible_professional_id', 'deleted_at', *_timestamps(),
        ),
        queryset=_episodes,
    ),
    Entity(
        name='visits',
        model=Visit,
        fields=(
            'id', 'episode_id', 'professional_id', 'appointment_id', 'occurred_at',
            'deleted_at', *_timestamps(),
        ),
        queryset=_visits,
    ),
    Entity(
        name='clinical_notes',
        model=ClinicalNote,
        # `content_hash` y `signed_at` van tal cual están guardados: con el JSON
        # canónico de `clinical/hashing.py` el sello se puede volver a comprobar
        # fuera de la aplicación (`verificar_firmas.py`).
        fields=(
            'id', 'visit_id', 'background', 'subjective', 'objective', 'inspection', 'palpation',
            'biomechanics', 'complementary_tests', 'assessment', 'plan', 'treatment_done',
            'follow_up', 'status', 'signed_by_id', 'signed_at', 'content_hash', 'deleted_at',
            *_timestamps(),
        ),
        queryset=_notes,
    ),
    Entity(
        name='addenda',
        model=Addendum,
        fields=('id', 'note_id', 'author_id', 'text', *_timestamps()),
        queryset=_addenda,
    ),
    Entity(
        name='questionnaire_responses',
        model=QuestionnaireResponse,
        # `snapshot` es la copia literal de pregunta + respuesta: se exporta eso, no
        # las preguntas actuales de la plantilla.
        fields=(
            'id', 'version_id', 'patient_id', 'episode_id', 'filled_at', 'source',
            'created_by_id', 'snapshot', 'deleted_at', *_timestamps(),
        ),
        queryset=_responses,
    ),
    Entity(
        name='clinical_alerts',
        model=ClinicalAlert,
        fields=(
            'id', 'patient_id', 'alert_type', 'severity', 'source', 'note', 'is_active',
            'created_by_id', 'source_response_id', 'deleted_at', *_timestamps(),
        ),
        queryset=_alerts,
    ),
    Entity(
        name='lesions',
        model=Lesion,
        # La zona clínica (`anatomical_zone`) y las coordenadas del dibujo (`x`, `y`)
        # van por separado, como en la base: nunca se deduce una de la otra.
        fields=(
            'id', 'episode_id', 'laterality', 'view', 'anatomical_zone', 'x', 'y',
            'lesion_type', 'status', 'detected_at', 'resolved_at', 'created_by_id',
            'deleted_at', *_timestamps(),
        ),
        queryset=_lesions,
    ),
    Entity(
        name='lesion_observations',
        model=LesionObservation,
        fields=(
            'id', 'lesion_id', 'visit_id', 'observed_at', 'length_mm', 'width_mm', 'depth_mm',
            'description', 'created_by_id', 'deleted_at', *_timestamps(),
        ),
        queryset=_observations,
    ),
    Entity(
        name='lesion_attachments',
        model=LesionAttachment,
        fields=(
            'id', 'public_id', 'observation_id', 'file', 'mime_type', 'size_bytes', 'checksum',
            'source', 'uploaded_at', 'deleted_at', *_timestamps(),
        ),
        queryset=_lesion_attachments,
        file=FileSpec('file', 'lesion-attachments', 'checksum'),
    ),
    Entity(
        name='performed_procedures',
        model=PerformedProcedure,
        # `frozen_*` son lo que costó entonces; `service_id` es solo procedencia.
        fields=(
            'id', 'visit_id', 'service_id', 'frozen_service_name', 'frozen_price', 'laterality',
            'affected_zone', 'performed_at', 'created_by_id', 'invoice_id', 'deleted_at',
            *_timestamps(),
        ),
        queryset=_procedures,
    ),
    Entity(
        name='signed_consents',
        model=SignedConsent,
        # `text_copy` es el texto literal que se firmó, no el de la versión actual.
        fields=(
            'id', 'public_id', 'version_id', 'patient_id', 'episode_id', 'text_copy',
            'signature_image', 'mime_type', 'size_bytes', 'checksum', 'signed_at',
            'signer_name', 'signer_relationship', 'deleted_at', *_timestamps(),
        ),
        queryset=_signed_consents,
        file=FileSpec('signature_image', 'consent-signatures', 'checksum'),
    ),
    Entity(
        name='patient_form_links',
        model=PatientFormLink,
        fields=(
            'id', 'patient_id', 'questionnaire_template_id', 'consent_template_id', 'episode_id',
            'episode_reason', 'expires_at', 'channel', 'sent_at', 'failed_attempts', 'revoked_at',
            'completed_at', 'completed_by_name', 'completed_by_relationship',
            'privacy_notice_version', 'questionnaire_response_id', 'signed_consent_id',
            *_timestamps(),
        ),
        clinic_only=('clinic_id', 'created_by_id', 'revoked_by_id'),
        excluded={'token_hash': 'secreto del enlace: ni siquiera guardado en claro'},
        queryset=_form_links,
    ),
    Entity(
        name='appointments',
        model=Appointment,
        fields=(
            'id', 'patient_id', 'service_id', 'professional_id', 'patient_phone', 'patient_name',
            'contact_email', 'booked_for_other', 'contact_name', 'contact_relationship',
            'service_name', 'scheduled_at', 'end_at', 'status', 'source', 'patient_confirmed_at',
            'cancelled_by', 'notes', 'cancellation_policy_hours', *_timestamps(),
        ),
        clinic_only=(
            'clinic_id', 'reminder_24h_sent', 'reminder_24h_sent_at', 'reminder_responded',
            'reminder_3h_sent', 'reminder_3h_sent_at',
        ),
        excluded={
            'confirmation_token': 'secreto: da acceso a confirmar o cancelar la cita',
            'hold_expires_at': 'estado operativo de la reserva provisional',
            'external_id': 'identificador de una integración externa',
            'external_calendar_id': 'identificador de una integración externa',
        },
        queryset=_appointments,
    ),
    Entity(
        name='appointment_status_history',
        model=AppointmentStatusHistory,
        fields=('id', 'appointment_id', 'from_status', 'to_status', 'actor', 'actor_label', 'changed_at'),
        queryset=_appointment_history,
    ),
    Entity(
        name='invoices',
        model=PatientInvoice,
        # `lines` es la copia de las líneas congelada al emitir.
        fields=(
            'id', 'patient_id', 'frozen_patient_name', 'status', 'number', 'issued_at', 'total',
            'lines', 'voided_at', 'void_reason', 'created_by_id', 'frozen_created_by_name',
            'deleted_at', *_timestamps(),
        ),
        clinic_only=('clinic_id',),
        queryset=_invoices,
    ),
    Entity(
        name='payments',
        model=Payment,
        fields=(
            'id', 'invoice_id', 'frozen_patient_name', 'frozen_invoice_number', 'receipt_number',
            'amount', 'method', 'paid_at', 'created_by_id', 'frozen_created_by_name',
            'deleted_at', *_timestamps(),
        ),
        clinic_only=('clinic_id',),
        queryset=_payments,
    ),
    Entity(
        name='conversations',
        model=ConversationSession,
        fields=(
            'id', 'phone', 'patient_id', 'guardian_id', 'last_interaction', 'last_message_at',
            'last_message_preview', 'last_staff_message_at', 'updated_at',
        ),
        clinic_only=('clinic_id',),
        excluded={
            'session_data': 'estado interno del agente de IA, no es contenido de la conversación',
            'appointment_context': 'estado interno del agente de IA',
            'unread_count': 'estado del panel',
            'is_test': 'las conversaciones de prueba no se exportan',
            'agent_paused': 'estado operativo del panel',
        },
        queryset=_conversations,
    ),
    Entity(
        name='chat_messages',
        model=ChatMessage,
        fields=(
            'id', 'session_id', 'direction', 'sender', 'body', 'message_type', 'media_url',
            'media_mime', 'wa_message_id', 'status', 'error_message', 'sent_at', 'created_at',
            'read_at', 'delivered_at', 'seen_at',
        ),
        # `raw` es la carga cruda de Meta: puede traer datos de terceros.
        clinic_only=('clinic_id', 'raw'),
        queryset=_messages,
    ),
    Entity(
        name='chat_attachments',
        model=ChatAttachment,
        fields=(
            'id', 'message_id', 'kind', 'file', 'mime_type', 'size_bytes', 'checksum',
            'uploaded_at',
        ),
        queryset=_chat_attachments,
        file=FileSpec('file', 'chat-media', 'checksum'),
    ),
    # --- solo la clínica entera ------------------------------------------------
    Entity(
        name='clinic',
        model=Clinic,
        fields=(
            'clinic_id', 'name', 'nif', 'is_active', 'phone', 'email', 'website',
            'privacy_policy_url', 'privacy_contact_email', 'address', 'city', 'province',
            'postal_code', 'country', 'timezone', 'hold_ttl_minutes', 'min_booking_notice_minutes',
            'description', 'logo_url', 'logo', 'whatsapp_phone_number_id',
            'whatsapp_business_account_id', 'agent_enabled', 'agent_handoff_timeout_seconds',
            'conversation_retention_months', 'api_type', 'api_url', 'calendly_link',
            'calendly_event_type_uuid', 'google_calendar_id', 'test_patient_id',
        ),
        excluded={
            'whatsapp_token': 'secreto',
            'whatsapp_verify_token': 'secreto',
            'agent_api_key': 'secreto',
            'api_key': 'secreto',
            'calendly_token': 'secreto',
        },
        queryset=_clinic,
        scopes=(CLINIC,),
        file=FileSpec('logo', 'clinic-logo'),
        pk_field='clinic_id',
    ),
    Entity(
        name='users',
        model=User,
        fields=(
            'id', 'email', 'first_name', 'last_name', 'role', 'is_active', 'clinic_id',
            'date_joined', 'created_at', 'updated_at',
        ),
        excluded={
            'password': 'secreto',
            'username': 'igual que el correo',
            'is_superuser': 'permiso interno de la plataforma',
            'is_staff': 'permiso interno de la plataforma',
            'last_login': 'dato de sesión, no del registro',
        },
        queryset=lambda scope: User.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='services',
        model=Service,
        fields=(
            'id', 'clinic_id', 'category_id', 'name', 'description', 'duration_minutes',
            'duration_type', 'duration_max_minutes', 'price', 'price_type', 'price_max', 'color',
            'is_active', *_timestamps(),
        ),
        queryset=lambda scope: Service.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='service_categories',
        model=ServiceCategory,
        fields=('id', 'clinic_id', 'name', 'color', 'is_active', *_timestamps()),
        queryset=lambda scope: ServiceCategory.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='professional_schedules',
        model=ProfessionalSchedule,
        fields=('id', 'professional_id', 'day_of_week', 'start_time', 'end_time', 'is_active'),
        queryset=_professional_schedules,
        scopes=(CLINIC,),
    ),
    Entity(
        name='professional_time_off',
        model=ProfessionalTimeOff,
        fields=('id', 'professional_id', 'starts_at', 'ends_at', 'reason', 'note'),
        queryset=_professional_time_off,
        scopes=(CLINIC,),
    ),
    Entity(
        name='professional_services',
        model=Professional.services.through,
        fields=('id', 'professional_id', 'service_id'),
        queryset=_professional_services,
        scopes=(CLINIC,),
    ),
    Entity(
        name='questionnaire_templates',
        model=QuestionnaireTemplate,
        fields=('id', 'clinic_id', 'name', 'specialty', 'is_active', 'deleted_at', *_timestamps()),
        queryset=_by_clinic(QuestionnaireTemplate),
        scopes=(CLINIC,),
    ),
    Entity(
        name='template_versions',
        model=TemplateVersion,
        fields=(
            'id', 'template_id', 'number', 'is_published', 'published_at', 'is_current',
            'deleted_at', *_timestamps(),
        ),
        queryset=_via(TemplateVersion, 'template__clinic_id'),
        scopes=(CLINIC,),
    ),
    Entity(
        name='questions',
        model=Question,
        fields=(
            'id', 'version_id', 'text', 'code', 'answer_type', 'order', 'is_required', 'options',
            'deleted_at', *_timestamps(),
        ),
        queryset=_via(Question, 'version__template__clinic_id'),
        scopes=(CLINIC,),
    ),
    Entity(
        name='consent_templates',
        model=ConsentTemplate,
        fields=('id', 'clinic_id', 'name', 'specialty', 'is_active', 'deleted_at', *_timestamps()),
        queryset=_by_clinic(ConsentTemplate),
        scopes=(CLINIC,),
    ),
    Entity(
        name='consent_versions',
        model=ConsentVersion,
        fields=(
            'id', 'template_id', 'number', 'text', 'is_published', 'published_at', 'is_current',
            'deleted_at', *_timestamps(),
        ),
        queryset=_via(ConsentVersion, 'template__clinic_id'),
        scopes=(CLINIC,),
    ),
    Entity(
        name='history_sequences',
        model=HistorySequence,
        fields=('id', 'clinic_id', 'year', 'last_value'),
        queryset=lambda scope: HistorySequence.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='invoice_sequences',
        model=InvoiceSequence,
        fields=('id', 'clinic_id', 'year', 'last_value'),
        queryset=lambda scope: InvoiceSequence.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='receipt_sequences',
        model=ReceiptSequence,
        fields=('id', 'clinic_id', 'year', 'last_value'),
        queryset=lambda scope: ReceiptSequence.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='knowledge_base',
        model=ClinicKnowledgeBase,
        fields=(
            'id', 'clinic_id', 'kb_type', 'title', 'content', 'metadata', 'active', 'created_at',
            'updated_at',
        ),
        queryset=lambda scope: ClinicKnowledgeBase.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='agent_profile',
        model=AgentProfile,
        fields=(
            'id', 'clinic_id', 'agent_name', 'tone', 'address_form', 'emoji_usage',
            'welcome_message', 'style_notes', 'updated_at', 'updated_by_id',
        ),
        queryset=lambda scope: AgentProfile.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
    Entity(
        name='change_log',
        model=ChangeLog,
        # Los campos clínicos van enmascarados en origen: el registro dice QUE cambiaron,
        # no su valor.
        fields=(
            'id', 'timestamp', 'user_id', 'user_repr', 'patient_id', 'ip', 'user_agent', 'origin',
            'content_type_id', 'object_id', 'object_repr', 'model_label', 'action', 'changes',
        ),
        queryset=_audit_rows(ChangeLog),
        scopes=(CLINIC,),
    ),
    Entity(
        name='access_log',
        model=AccessLog,
        fields=(
            'id', 'timestamp', 'user_id', 'user_repr', 'patient_id', 'ip', 'user_agent', 'origin',
            'content_type_id', 'object_id', 'object_repr', 'action', 'path', 'method',
            'result_count', 'reason',
        ),
        queryset=_audit_rows(AccessLog),
        scopes=(CLINIC,),
    ),
    Entity(
        name='whatsapp_templates',
        model=WhatsAppTemplate,
        fields=(
            'id', 'clinic_id', 'name', 'language', 'category', 'header_text', 'body', 'footer',
            'body_examples', 'purpose', 'variable_map', 'status', 'rejection_reason',
            'meta_template_id', 'has_unsupported_components', 'submitted_at', 'status_synced_at',
            'updated_by_id', 'created_at', 'updated_at',
        ),
        queryset=lambda scope: WhatsAppTemplate.objects.filter(clinic_id=scope.clinic.pk),
        scopes=(CLINIC,),
    ),
)

#: Modelos que no se exportan nunca, y por qué. Va al `manifest.json` y al LEEME.
EXCLUDED_MODELS: dict[str, str] = {
    'core.TOTPDevice': 'credencial de acceso (verificación en dos pasos)',
    'core.RecoveryCode': 'credencial de acceso (verificación en dos pasos)',
    'notifications.PushSubscription': 'datos técnicos de dispositivos del personal',
    'notifications.Notification': 'avisos internos del panel',
    'notifications.NotificationPreference': 'preferencias del personal',
    'agent.AgentMemory': 'contexto interno del agente de IA',
    'agent.WorkflowError': 'registro técnico de errores de automatización',
    'knowledge.ClinicInfoCache': 'caché técnica',
    'knowledge.ClinicInfoQuery': 'registro técnico de consultas del agente',
    'billing.Subscription': 'contrato de la plataforma con la clínica, no datos del paciente',
}


def entities_for(scope: Scope) -> list[Entity]:
    return [entity for entity in ENTITIES if scope.kind in entity.scopes]


#: Las entidades por nombre: el PDF lee con las mismas consultas que el ZIP, así que
#: las dos copias no pueden diferir en qué filas incluyen.
BY_NAME: dict[str, Entity] = {entity.name: entity for entity in ENTITIES}


def queryset_for(name: str, scope: Scope) -> QuerySet:
    return BY_NAME[name].queryset(scope)
