"""Un expediente clínico completo, para comprobar qué sale en una exportación.

`build_file(...)` monta, para un paciente de una clínica, un dato de cada cosa que
la exportación debe llevarse: historia, nota firmada, adenda, lesiones (una de
ellas borrada lógicamente), foto, consentimiento firmado con su firma, anamnesis,
alerta, factura emitida con cobro, cita, contacto responsable y un chat con foto.
Cada texto lleva una etiqueta (`tag`) para poder comprobar que lo de una clínica
no aparece en la exportación de otra.
"""
import zlib
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone

from agent.media import attach_media
from agent.models import ChatMessage, ConversationSession
from agent.services import record_message
from appointments.models import Appointment
from billing.models import PatientInvoice, Payment
from clinical.models import (
    Addendum,
    ClinicalAlert,
    ClinicalNote,
    ConsentTemplate,
    ConsentVersion,
    Episode,
    LesionAttachment,
    LesionObservation,
    PerformedProcedure,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    SignedConsent,
    TemplateVersion,
    Visit,
)
from patients.models import Guardian, PatientGuardian

from tests.agent.test_chat_media import jpeg_with_exif
from tests.agent.test_chat_media import upload as chat_upload
from tests.clinical.test_consents import signature
from tests.clinical.test_lesion_attachments import upload as photo_upload
from tests.clinical.test_lesions import make_lesion


def _phone(tag, salt):
    """Un móvil distinto por etiqueta y uso: hay unicidad por (clínica, teléfono)."""
    return f'+346{zlib.crc32(f"{tag}{salt}".encode()) % 10**8:08d}'


def build_file(clinic, user, patient, service, tag):
    professional = user.professional_profile
    professional.services.add(service)

    episode = Episode.objects.create(
        history=patient.medical_history, reason=f'Motivo {tag}',
        responsible_professional=professional,
    )
    visit = Visit.objects.create(episode=episode, professional=professional)
    note = ClinicalNote.objects.create(
        visit=visit, subjective=f'Subjetivo {tag}', objective=f'Objetivo {tag}',
        assessment=f'Valoración {tag}', plan=f'Plan {tag}',
        treatment_done=f'Tratamiento {tag}',
    )
    note.sign(professional)
    addendum = Addendum.objects.create(note=note, author=professional, text=f'Adenda {tag}')

    lesion = make_lesion(episode)
    observation = LesionObservation.objects.create(
        lesion=lesion, visit=visit, description=f'Observación {tag}', length_mm=Decimal('4.5'),
    )
    attachment = LesionAttachment.objects.create(observation=observation, file=photo_upload())

    # Una lesión dada de baja lógicamente, con su observación: forma parte del registro.
    gone = make_lesion(episode, x=0.2, y=0.3)
    gone_observation = LesionObservation.objects.create(
        lesion=gone, visit=visit, description=f'Borrada {tag}',
    )
    gone.delete()

    template = ConsentTemplate.objects.create(clinic=clinic, name=f'Consentimiento {tag}')
    version = ConsentVersion.objects.create(template=template, text=f'Texto consentimiento {tag}')
    version.publish()
    consent = SignedConsent.sign(
        version=version, patient=patient, episode=episode, signature_image=signature(),
    )

    q_template = QuestionnaireTemplate.objects.create(clinic=clinic, name=f'Anamnesis {tag}')
    q_version = TemplateVersion.objects.create(template=q_template)
    question = Question.objects.create(
        version=q_version, text=f'¿Pregunta {tag}?', answer_type=Question.AnswerType.TEXT, order=1,
    )
    q_version.publish()
    response = QuestionnaireResponse.record(
        version=q_version, patient=patient, episode=episode,
        answers={question.pk: f'Respuesta {tag}'},
    )

    alert = ClinicalAlert.objects.create(
        patient=patient, alert_type=ClinicalAlert.AlertType.ALLERGY_LATEX, note=f'Alerta {tag}',
    )

    procedure = PerformedProcedure.objects.create(visit=visit, service=service)
    invoice = PatientInvoice.objects.create(clinic=clinic, patient=patient)
    invoice.add_procedure(procedure)
    invoice = invoice.issue()
    payment = Payment.objects.create(invoice=invoice, amount=Decimal('10.00'), method=Payment.Method.CARD)

    when = timezone.now() + timedelta(days=2)
    appointment = Appointment.objects.create(
        clinic=clinic, patient=patient, service=service, professional=professional,
        scheduled_at=when, end_at=when + timedelta(minutes=30),
        status=Appointment.Status.CONFIRMED, notes=f'Cita {tag}',
    )

    guardian = Guardian.objects.create(
        clinic=clinic, first_name=f'Tutor{tag}', last_name='Apellido', phone=_phone(tag, 'tutor'),
        email=f'tutor-{tag}@example.com',
    )
    PatientGuardian.objects.create(patient=patient, guardian=guardian, relationship='parent')

    session = ConversationSession.objects.create(
        clinic=clinic, phone=_phone(tag, 'chat'), patient=patient,
    )
    message = record_message(
        clinic=clinic, session=session, direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT, body=f'Mensaje {tag}', message_type='image',
    )
    chat_attachment = attach_media(message, chat_upload(jpeg_with_exif()))

    return SimpleNamespace(
        professional=professional, episode=episode, visit=visit, note=note, addendum=addendum,
        lesion=lesion, gone_lesion=gone, gone_observation=gone_observation,
        observation=observation, attachment=attachment, consent=consent, response=response,
        alert=alert, procedure=procedure, invoice=invoice, payment=payment,
        appointment=appointment, guardian=guardian, session=session, message=message,
        chat_attachment=chat_attachment,
    )
