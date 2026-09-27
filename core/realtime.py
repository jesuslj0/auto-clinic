"""Piezas comunes del tiempo real (Channels) acotado por clínica."""

import hashlib
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def clinic_group_name(stream: str, clinic_id: str) -> str:
    """Nombre del grupo de Channels de una clínica para un flujo de eventos.

    Se deriva con un hash en vez de concatenar el id: Channels exige que el
    nombre case con `[a-zA-Z0-9_.-]{1,100}`, y `Clinic.clinic_id` es texto libre
    de hasta 100 caracteres que con el prefijo podría no caber o no casar.
    """
    digest = hashlib.blake2s(str(clinic_id).encode(), digest_size=8).hexdigest()
    return f'{stream}.{digest}'


def send_to_group(group: str, event: dict) -> None:
    """Emite un evento a un grupo sin dejar que un fallo de Redis se propague.

    El aviso en vivo es un extra: si la capa de canales está caída, el mensaje
    ya está guardado y el cliente lo recupera al reconectar. Nunca debe tumbar
    la petición que lo originó (la ingesta de n8n, un envío del staff).
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(group, event)
    except Exception:
        logger.exception('No se pudo emitir el evento %s al grupo %s', event.get('type'), group)
