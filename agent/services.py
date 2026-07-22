"""Lógica de escritura del historial de chat.

Vive aquí —y no en el serializer— porque tanto la API que alimenta n8n como
cualquier vista futura del panel deben pasar por el mismo sitio para mantener
coherentes la sesión, sus contadores denormalizados y el hilo de mensajes.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.whatsapp import WhatsAppError, send_text
from patients.models import Patient
from patients.services import normalize_phone_safe

PREVIEW_MAX_LENGTH = 280

# Texto que se muestra en la lista de chats cuando el mensaje no es texto.
_NON_TEXT_PREVIEW = {
    ChatMessage.MessageType.IMAGE: '📷 Imagen',
    ChatMessage.MessageType.AUDIO: '🎤 Audio',
    ChatMessage.MessageType.VIDEO: '🎬 Vídeo',
    ChatMessage.MessageType.DOCUMENT: '📄 Documento',
    ChatMessage.MessageType.LOCATION: '📍 Ubicación',
}


def build_preview(message: ChatMessage) -> str:
    """Resumen de una línea del mensaje para la lista de conversaciones."""
    if message.body.strip():
        return message.body.strip()[:PREVIEW_MAX_LENGTH]
    return _NON_TEXT_PREVIEW.get(message.message_type, '')


def get_or_create_session(clinic, phone: str, *, is_test: bool = False) -> ConversationSession:
    """Devuelve la conversación de ese número en esa clínica, creándola si hace falta.

    El teléfono se normaliza a E.164 con las mismas reglas que los pacientes,
    para que `+34600111222`, `600 111 222` y `0034600111222` caigan en el mismo
    hilo en vez de abrir tres.

    `is_test` marca el hilo como banco de pruebas del panel. La marca solo se
    pone, nunca se quita: si el número de prueba escribe luego por WhatsApp de
    verdad, el hilo sigue siendo el de pruebas y no aparece en la bandeja.
    """
    normalized = normalize_phone_safe(phone) or phone.strip()

    session, created = ConversationSession.objects.get_or_create(
        clinic=clinic,
        phone=normalized,
        defaults={'is_test': is_test},
    )
    if is_test and not session.is_test:
        session.is_test = True
        session.save(update_fields=['is_test'])

    # Vinculamos con la ficha del paciente cuando el número coincide. Se
    # reintenta mientras no haya vínculo: el paciente puede darse de alta
    # después de la primera conversación.
    if session.patient_id is None:
        patient = Patient.objects.filter(clinic=clinic, phone=normalized).first()
        if patient is not None:
            session.patient = patient
            session.save(update_fields=['patient'])

    return session


@transaction.atomic
def record_message(
    *,
    clinic,
    phone: str = '',
    session: ConversationSession | None = None,
    direction: str,
    sender: str,
    body: str = '',
    message_type: str = ChatMessage.MessageType.TEXT,
    media_url: str = '',
    media_mime: str = '',
    wa_message_id: str | None = None,
    status: str = '',
    error_message: str = '',
    raw=None,
    sent_at=None,
) -> ChatMessage:
    """Registra un mensaje en el hilo y actualiza la cabecera de la conversación.

    Idempotente: si `wa_message_id` ya está registrado devuelve el mensaje
    existente sin duplicarlo ni volver a tocar los contadores. Meta reintenta
    la entrega de sus webhooks, así que sin esto el hilo se llena de repetidos.
    """
    if session is None:
        if not phone:
            raise ValueError('Hace falta `phone` o `session` para registrar el mensaje.')
        session = get_or_create_session(clinic, phone)

    if wa_message_id:
        existing = ChatMessage.objects.filter(wa_message_id=wa_message_id).first()
        if existing is not None:
            return existing

    message = ChatMessage.objects.create(
        clinic=clinic,
        session=session,
        direction=direction,
        sender=sender,
        body=body,
        message_type=message_type,
        media_url=media_url,
        media_mime=media_mime,
        wa_message_id=wa_message_id or None,
        status=status,
        error_message=error_message,
        raw=raw,
        sent_at=sent_at,
    )

    now = timezone.now()
    session.last_message_at = message.sent_at or message.created_at or now
    session.last_message_preview = build_preview(message)

    fields = ['last_message_at', 'last_message_preview']
    if direction == ChatMessage.Direction.INBOUND:
        # F() en vez de sumar en Python: el webhook puede entregar dos mensajes
        # a la vez y la suma tiene que resolverse en la base de datos.
        session.unread_count = F('unread_count') + 1
        session.last_interaction = now
        fields += ['unread_count', 'last_interaction']
    elif sender == ChatMessage.Sender.STAFF:
        # Abre la pausa temporal del agente: si contesta una persona, el bot se
        # aparta hasta que pase el plazo de inactividad de la clínica.
        session.last_staff_message_at = now
        fields += ['last_staff_message_at']

    session.save(update_fields=fields + ['updated_at'])
    # Tras un F(), el atributo guarda la expresión y no el número: lo recargamos
    # para que quien reciba el mensaje pueda leer el contador.
    session.refresh_from_db(fields=['unread_count'])
    return message


def resolve_test_phone(clinic) -> str:
    """Teléfono con el que el panel simula ser el paciente de prueba.

    Se usa el del paciente de prueba para que el agente lo reconozca; sin uno
    configurado queda un literal que no colisiona con ningún número real.
    """
    test_patient = clinic.test_patient
    return (test_patient.phone if test_patient and test_patient.phone else '') or 'panel-test'


def get_test_session(clinic) -> ConversationSession | None:
    """Hilo de pruebas de la clínica, si ya se ha usado el chat del panel."""
    phone = resolve_test_phone(clinic)
    normalized = normalize_phone_safe(phone) or phone.strip()
    return ConversationSession.objects.filter(
        clinic=clinic, phone=normalized, is_test=True
    ).first()


def mark_session_read(session: ConversationSession) -> None:
    """Pone a cero los no leídos y sella los mensajes entrantes pendientes."""
    now = timezone.now()
    session.messages.filter(
        direction=ChatMessage.Direction.INBOUND, read_at__isnull=True
    ).update(read_at=now)
    ConversationSession.objects.filter(pk=session.pk).update(unread_count=0)


def send_staff_message(*, session: ConversationSession, body: str) -> ChatMessage:
    """Envía por WhatsApp un mensaje escrito por una persona y lo deja en el hilo.

    Va directo a la Cloud API en lugar de pasar por n8n: si el bot está caído,
    la clínica tiene que poder seguir hablando con sus pacientes.

    El mensaje se registra siempre, salga o no. Si Meta lo rechaza queda como
    `failed` con el motivo, para que en el panel se vea que no llegó en vez de
    aparentar que sí.
    """
    body = (body or '').strip()
    if not body:
        raise ValueError('El mensaje no puede estar vacío.')

    if session.clinic is None:
        raise WhatsAppError('Esta conversación no está asociada a ninguna clínica.')

    # Se comprueba antes de registrar nada: fuera de la ventana el envío es
    # imposible, así que no tiene sentido dejar una burbuja fallida en el hilo.
    if not session.can_send_free_text:
        raise WhatsAppError(
            'Han pasado más de 24 horas desde el último mensaje del paciente. '
            'WhatsApp ya no permite responder con texto libre en esta conversación.'
        )

    message = record_message(
        clinic=session.clinic,
        session=session,
        direction=ChatMessage.Direction.OUTBOUND,
        sender=ChatMessage.Sender.STAFF,
        body=body,
        status=ChatMessage.Status.QUEUED,
    )

    # La llamada HTTP va fuera de la transacción de `record_message`: mantener
    # abierta una transacción mientras se espera a un tercero es pedir bloqueos.
    try:
        wa_message_id = send_text(session.clinic, session.phone, body)
    except WhatsAppError as exc:
        ChatMessage.objects.filter(pk=message.pk).update(
            status=ChatMessage.Status.FAILED, error_message=str(exc)
        )
        message.status = ChatMessage.Status.FAILED
        message.error_message = str(exc)
        raise

    ChatMessage.objects.filter(pk=message.pk).update(
        status=ChatMessage.Status.SENT,
        wa_message_id=wa_message_id or None,
        sent_at=timezone.now(),
    )
    message.refresh_from_db()
    return message
