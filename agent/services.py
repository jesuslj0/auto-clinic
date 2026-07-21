"""Lógica de escritura del historial de chat.

Vive aquí —y no en el serializer— porque tanto la API que alimenta n8n como
cualquier vista futura del panel deben pasar por el mismo sitio para mantener
coherentes la sesión, sus contadores denormalizados y el hilo de mensajes.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
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


def get_or_create_session(clinic, phone: str) -> ConversationSession:
    """Devuelve la conversación de ese número en esa clínica, creándola si hace falta.

    El teléfono se normaliza a E.164 con las mismas reglas que los pacientes,
    para que `+34600111222`, `600 111 222` y `0034600111222` caigan en el mismo
    hilo en vez de abrir tres.
    """
    normalized = normalize_phone_safe(phone) or phone.strip()

    session, created = ConversationSession.objects.get_or_create(
        clinic=clinic,
        phone=normalized,
    )

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

    session.save(update_fields=fields + ['updated_at'])
    # Tras un F(), el atributo guarda la expresión y no el número: lo recargamos
    # para que quien reciba el mensaje pueda leer el contador.
    session.refresh_from_db(fields=['unread_count'])
    return message


def mark_session_read(session: ConversationSession) -> None:
    """Pone a cero los no leídos y sella los mensajes entrantes pendientes."""
    now = timezone.now()
    session.messages.filter(
        direction=ChatMessage.Direction.INBOUND, read_at__isnull=True
    ).update(read_at=now)
    ConversationSession.objects.filter(pk=session.pk).update(unread_count=0)
