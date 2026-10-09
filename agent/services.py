"""Lógica de escritura del historial de chat.

Vive aquí —y no en el serializer— porque tanto la API que alimenta n8n como
cualquier vista futura del panel deben pasar por el mismo sitio para mantener
coherentes la sesión, sus contadores denormalizados y el hilo de mensajes.
"""

import logging
import os

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from agent.models import ChatAttachment, ChatMessage, ConversationSession
from agent.realtime import broadcast_message, broadcast_session
from agent.files import is_chat_audio
from agent.whatsapp import WhatsAppError, send_media, send_text, send_typing, upload_media
from patients.models import Guardian, Patient
from patients.services import normalize_phone_safe

logger = logging.getLogger(__name__)

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
    if message.display_body:
        return message.display_body[:PREVIEW_MAX_LENGTH]
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
        elif session.guardian_id is None:
            # Sin ficha con ese teléfono: puede ser el de un contacto responsable.
            guardian = Guardian.objects.filter(clinic=clinic, phone=normalized).first()
            if guardian is not None:
                session.guardian = guardian
                session.save(update_fields=['guardian'])

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

    broadcast_message(message)
    broadcast_session(session)
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
    # En bloque a propósito: `read_at` está excluido de la auditoría (ver
    # `AgentConfig.ready`), así que este `update()` no se salta nada.
    session.messages.filter(
        direction=ChatMessage.Direction.INBOUND, read_at__isnull=True
    ).update(read_at=now)
    ConversationSession.objects.filter(pk=session.pk).update(unread_count=0)
    session.unread_count = 0
    broadcast_session(session)


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
        # `save()` y no `queryset.update()`: el mensaje está en la auditoría y un
        # `update()` se saltaría las señales que la alimentan.
        message.status = ChatMessage.Status.FAILED
        message.error_message = str(exc)
        message.save(update_fields=['status', 'error_message'])
        broadcast_message(message)
        raise

    message.status = ChatMessage.Status.SENT
    message.wa_message_id = wa_message_id or None
    message.sent_at = timezone.now()
    message.save(update_fields=['status', 'wa_message_id', 'sent_at'])
    broadcast_message(message)
    return message


#: Lo que WhatsApp acepta como imagen (el WebP solo vale como sticker).
_WHATSAPP_IMAGE_TYPES = {'image/jpeg', 'image/png'}

#: Tope de Meta para el pie de una imagen.
MEDIA_CAPTION_MAX_LENGTH = 1024


def send_staff_media(*, session: ConversationSession, file, caption: str = '') -> ChatMessage:
    """Envía por WhatsApp una imagen o un audio del staff y lo deja en el hilo.

    Mismo criterio que `send_staff_message`: directo a la Cloud API, sin pasar
    por n8n, y el mensaje queda registrado salga o no. Esto otro es lo que lo
    distingue:

    - El fichero pasa por la misma validación y limpieza que los que manda un
      paciente (contenido, no extensión; imágenes sin metadatos) y se guarda en
      el bucket privado como `ChatAttachment`. A Meta se le sube **lo guardado**,
      así que lo que ve el paciente es lo que queda en el historial.
    - Si el fichero no es válido no queda nada: mensaje y adjunto se crean en
      la misma transacción.
    - Solo las imágenes admiten texto (como pie de foto); un audio con texto se
      rechaza en vez de perder el texto sin avisar.
    - El agente no se entera: este mensaje no toca su memoria ni va a n8n.
    """
    caption = (caption or '').strip()

    if session.clinic is None:
        raise WhatsAppError('Esta conversación no está asociada a ninguna clínica.')
    if not session.can_send_free_text:
        raise WhatsAppError(
            'Han pasado más de 24 horas desde el último mensaje del paciente. '
            'WhatsApp ya no permite responder con texto libre en esta conversación.'
        )

    is_audio = is_chat_audio(file)
    if is_audio and caption:
        raise ValueError('Los audios no admiten texto. Envía el texto en un mensaje aparte.')
    if len(caption) > MEDIA_CAPTION_MAX_LENGTH:
        raise ValueError(f'El pie de foto admite como máximo {MEDIA_CAPTION_MAX_LENGTH} caracteres.')

    kind = ChatAttachment.Kind.AUDIO if is_audio else ChatAttachment.Kind.IMAGE
    message_type = ChatMessage.MessageType.AUDIO if is_audio else ChatMessage.MessageType.IMAGE

    with transaction.atomic():
        message = record_message(
            clinic=session.clinic,
            session=session,
            direction=ChatMessage.Direction.OUTBOUND,
            sender=ChatMessage.Sender.STAFF,
            body=caption,
            message_type=message_type,
            status=ChatMessage.Status.QUEUED,
        )
        # Si el fichero no vale, `save()` lanza `ValidationError` y se deshace
        # también el mensaje.
        attachment = ChatAttachment(message=message, kind=kind, file=file)
        attachment.save()
        if kind == ChatAttachment.Kind.IMAGE and attachment.mime_type not in _WHATSAPP_IMAGE_TYPES:
            raise ValueError('WhatsApp solo admite imágenes JPEG o PNG.')

    try:
        with attachment.file.open('rb') as stored:
            content = stored.read()
        extension = os.path.splitext(attachment.file.name)[1]
        media_id = upload_media(session.clinic, content, attachment.mime_type, f'{kind}{extension}')
        wa_message_id = send_media(session.clinic, session.phone, kind, media_id, caption)
    except WhatsAppError as exc:
        message.status = ChatMessage.Status.FAILED
        message.error_message = str(exc)
        message.save(update_fields=['status', 'error_message'])
        broadcast_message(message)
        raise

    message.status = ChatMessage.Status.SENT
    message.wa_message_id = wa_message_id or None
    message.sent_at = timezone.now()
    message.save(update_fields=['status', 'wa_message_id', 'sent_at'])
    broadcast_message(message)
    return message


#: Cada cuánto, como mucho, se repite el «escribiendo…» de una conversación.
#: Meta lo mantiene 25 s: repetirlo a los 20 lo deja encendido mientras alguien
#: siga escribiendo, sin una llamada a Meta por tecla ni por pestaña abierta.
STAFF_TYPING_INTERVAL_SECONDS = 20


def signal_staff_typing(session: ConversationSession) -> bool:
    """Enseña «escribiendo…» al paciente mientras el staff redacta. `True` si salió.

    Nunca lanza: es un adorno, y un fallo de Meta no debe molestar a quien
    escribe (se deja en el log y ya). No se envía nada si:

    - la ventana de 24 h está cerrada (tampoco se podría responder);
    - no hay un mensaje del paciente con id de WhatsApp al que engancharlo
      (Meta lo exige);
    - ya se envió hace menos de `STAFF_TYPING_INTERVAL_SECONDS` en este hilo.

    Ojo: la llamada marca como leído ese mensaje del paciente en su WhatsApp.
    Aquí es verdad —quien escribe la respuesta lo ha leído—.
    """
    if session.clinic is None or not session.can_send_free_text:
        return False

    last_inbound = (
        session.messages
        .filter(direction=ChatMessage.Direction.INBOUND, wa_message_id__isnull=False)
        .exclude(wa_message_id='')
        .order_by('-created_at', '-id')
        .values_list('wa_message_id', flat=True)
        .first()
    )
    if not last_inbound:
        return False

    # `add` solo escribe si la clave no existe: es el candado del intervalo, y
    # dos pestañas del mismo hilo no disparan dos llamadas. Con la caché por
    # defecto (memoria del proceso) el candado es por proceso; con varios
    # procesos se colaría alguna llamada de más, sin más consecuencia.
    if not cache.add(f'agent:staff-typing:{session.pk}', 1, STAFF_TYPING_INTERVAL_SECONDS):
        return False

    try:
        send_typing(session.clinic, last_inbound)
    except WhatsAppError as exc:
        logger.info('No se pudo enviar «escribiendo…» (sesión %s): %s', session.pk, exc)
        return False
    return True


# ---------------------------------------------------------------------------
# Acuses de entrega (webhook `statuses` de Meta)
# ---------------------------------------------------------------------------

#: Orden de los estados de un saliente. Meta no garantiza el orden de llegada
#: (un «read» puede adelantarse a su «delivered») y reintenta sus webhooks, así
#: que un estado solo se aplica si AVANZA: nunca se vuelve de leído a entregado.
_STATUS_RANK = {
    '': 0,
    ChatMessage.Status.QUEUED: 1,
    ChatMessage.Status.SENT: 2,
    ChatMessage.Status.DELIVERED: 3,
    ChatMessage.Status.READ: 4,
}

DELIVERY_STATUSES = (
    ChatMessage.Status.SENT,
    ChatMessage.Status.DELIVERED,
    ChatMessage.Status.READ,
    ChatMessage.Status.FAILED,
)


def apply_delivery_status(*, clinic, wa_message_id: str, status: str, timestamp=None, error: str = ''):
    """Aplica un acuse de WhatsApp a un mensaje saliente de la clínica.

    Devuelve el mensaje, o `None` si no hay ningún saliente de esa clínica con
    ese `wa_message_id` (un acuse de otra clínica cae aquí: no se distingue de
    uno desconocido). Idempotente: repetir el mismo acuse no cambia nada.

    - Las marcas de tiempo se rellenan una sola vez y con la hora de Meta. Un
      «read» implica también «delivered»: si llega primero, sella las dos.
    - «failed» solo se aplica si el mensaje aún no consta como entregado: un
      mensaje que ya llegó no puede dejar de haber llegado.
    """
    when = timestamp or timezone.now()

    with transaction.atomic():
        message = (
            ChatMessage.objects.select_for_update()
            .filter(clinic=clinic, wa_message_id=wa_message_id, direction=ChatMessage.Direction.OUTBOUND)
            .first()
        )
        if message is None:
            return None

        fields = []
        current_rank = _STATUS_RANK.get(message.status, 0)

        if status == ChatMessage.Status.FAILED:
            if message.status != ChatMessage.Status.FAILED and current_rank < _STATUS_RANK[ChatMessage.Status.DELIVERED]:
                message.status = ChatMessage.Status.FAILED
                message.error_message = (error or 'WhatsApp no pudo entregar el mensaje.')[:1000]
                fields += ['status', 'error_message']
        else:
            if message.status != ChatMessage.Status.FAILED and _STATUS_RANK[status] > current_rank:
                message.status = status
                fields.append('status')
            if status == ChatMessage.Status.SENT and message.sent_at is None:
                message.sent_at = when
                fields.append('sent_at')
            if status in (ChatMessage.Status.DELIVERED, ChatMessage.Status.READ) and message.delivered_at is None:
                message.delivered_at = when
                fields.append('delivered_at')
            if status == ChatMessage.Status.READ and message.seen_at is None:
                message.seen_at = when
                fields.append('seen_at')

        if fields:
            # `save()` y no `update()`: el mensaje está en la auditoría.
            message.save(update_fields=fields)
            broadcast_message(message)
    return message
