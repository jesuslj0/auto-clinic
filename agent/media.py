"""Entrada y salida de los adjuntos de chat (fotos y notas de voz).

Entrada (`attach_media`): n8n descarga el binario de Meta y lo sube a
`POST /api/agent/messages/<id>/media/`. Una sola vez por mensaje.

Salida (`signed_media_url`): el panel lo pide a `GET /chats/adjuntos/<id>/`, que
comprueba el permiso, deja `AccessLog` y redirige a una URL firmada del bucket
privado. Mismo principio que `clinical/attachments.py`: comprobar y firmar
viven en la misma función, para que no exista un camino que firme sin mirar.

Diferencias con los adjuntos clínicos, a propósito:

- **El permiso es de la clínica del hilo, no del paciente.** Quien escribe por
  primera vez todavía no tiene ficha, y su foto se tiene que poder ver igual.
- **La URL vive menos** (`CHAT_MEDIA_URL_EXPIRE`, 5 minutos por defecto) y pide
  al bucket que la respuesta no se guarde en la caché del navegador: la foto de
  una herida no debe quedarse en el disco del móvil o del ordenador de la
  clínica.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import storages
from django.db import IntegrityError, transaction

from agent.models import ChatAttachment, ChatMessage
from agent.realtime import broadcast_message
from audit.mixins import log_access
from audit.models import AccessLog

DEFAULT_MEDIA_URL_EXPIRE = 300

#: Cabeceras que el bucket pone en SU respuesta (no en la nuestra). Van dentro
#: de la firma, así que no se pueden quitar de la URL sin invalidarla.
NO_STORE = 'private, no-store, max-age=0'


class MediaAlreadyAttached(Exception):
    """El mensaje ya tiene su adjunto. No se reemplaza."""


_KIND_BY_MESSAGE_TYPE = {
    ChatMessage.MessageType.IMAGE: ChatAttachment.Kind.IMAGE,
    ChatMessage.MessageType.AUDIO: ChatAttachment.Kind.AUDIO,
}


def media_url_expire() -> int:
    return getattr(settings, 'CHAT_MEDIA_URL_EXPIRE', DEFAULT_MEDIA_URL_EXPIRE)


def attach_media(message: ChatMessage, uploaded_file) -> ChatAttachment:
    """Guarda el adjunto de un mensaje entrante. Una vez y para siempre.

    Lanza `ValidationError` si el mensaje no admite adjunto o el fichero no
    pasa la validación, y `MediaAlreadyAttached` si ya tenía uno.
    """
    kind = _KIND_BY_MESSAGE_TYPE.get(message.message_type)
    if kind is None:
        raise ValidationError('Este tipo de mensaje no admite adjunto (solo imagen o audio).')
    if message.direction != ChatMessage.Direction.INBOUND:
        raise ValidationError('Solo se guardan adjuntos que manda el paciente.')

    with transaction.atomic():
        # Bloquea el mensaje: dos subidas simultáneas (un reintento de n8n) se
        # ordenan aquí y la segunda ve el adjunto de la primera.
        ChatMessage.objects.select_for_update().filter(pk=message.pk).exists()
        if ChatAttachment.objects.filter(message=message).exists():
            raise MediaAlreadyAttached(f'El mensaje {message.pk} ya tiene adjunto.')
        try:
            with transaction.atomic():
                attachment = ChatAttachment(message=message, kind=kind, file=uploaded_file)
                attachment.save()
        except IntegrityError as exc:
            raise MediaAlreadyAttached(f'El mensaje {message.pk} ya tiene adjunto.') from exc
        # La burbuja ya está pintada sin la foto: se avisa para que la repinte.
        broadcast_message(message)
    return attachment


def can_view_chat_media(user, attachment: ChatAttachment) -> bool:
    """`True` si `user` puede ver ese adjunto.

    1. El agente, nunca: el token de n8n sube adjuntos pero no los lee.
    2. Sin sesión o con la cuenta desactivada, tampoco.
    3. El superusuario, todas las clínicas (como en el resto del proyecto).
    4. El resto, solo los de su clínica. Sin clínica asignada, ninguno.
    """
    from core.authentication import ClinicAgent

    if isinstance(user, ClinicAgent):
        return False
    if user is None or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if not getattr(user, 'clinic_id', None):
        return False
    return user.clinic_id == attachment.message.clinic_id


def signed_media_url(attachment: ChatAttachment, user) -> str:
    """URL firmada y de vida corta del adjunto, o `PermissionDenied`.

    Es el ÚNICO camino hasta el contenido. La URL no se guarda en ninguna parte.
    """
    if not can_view_chat_media(user, attachment):
        raise PermissionDenied('No tiene permiso para ver este adjunto.')

    backend = storages['clinical_media']
    name = attachment.file.name
    try:
        from storages.backends.s3 import S3Storage
    except ImportError:  # pragma: no cover - django-storages está en requirements
        S3Storage = None

    if S3Storage is not None and isinstance(backend, S3Storage):
        return backend.url(
            name,
            parameters={
                'ResponseCacheControl': NO_STORE,
                'ResponseContentType': attachment.mime_type,
                'ResponseContentDisposition': 'inline',
            },
            expire=media_url_expire(),
        )
    # Otros backends (tests en memoria) no firman: la URL es la que sea.
    return backend.url(name)


def log_media_view(attachment: ChatAttachment, request=None):
    """Deja constancia en `AccessLog`: ver la foto es un acceso a dato de salud."""
    return log_access(
        action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
        obj=attachment,
        patient=attachment.patient,
        request=request,
    )
