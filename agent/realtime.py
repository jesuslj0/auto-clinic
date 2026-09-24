"""Avisos en vivo del panel de chats.

Se emiten desde los servicios (`record_message`, `mark_session_read`…) y no
desde señales `post_save`: una señal saltaría antes de que se actualice la
cabecera de la sesión y dentro de la transacción. Todo sale con
`transaction.on_commit`, para no avisar nunca de una fila que aún no existe.

El payload es mínimo a propósito: ni el cuerpo del mensaje ni el nombre del
paciente viajan por el socket. El cliente pide el fragmento renderizado.

Los hilos de prueba (`is_test`) no se emiten: la bandeja no los muestra.

Cada aviso lleva `total_unread`, los no leídos de toda la clínica, para que el
contador del menú se actualice en cualquier página sin pedir nada al servidor.
"""

from django.db import transaction
from django.db.models import Sum

from core.realtime import clinic_group_name, send_to_group

from agent.consumers import CHATS_STREAM
from agent.models import ConversationSession


def _isoformat(value):
    return value.isoformat() if value else None


def clinic_unread_total(clinic_id) -> int:
    """No leídos de la bandeja de una clínica (sin los hilos de prueba)."""
    return (
        ConversationSession.objects.filter(clinic_id=clinic_id, is_test=False)
        .aggregate(total=Sum('unread_count'))['total']
        or 0
    )


def _emit(clinic_id, handler, payload):
    group = clinic_group_name(CHATS_STREAM, clinic_id)

    def send():
        # El total se cuenta ya confirmado el commit, no al registrar el aviso:
        # si en la misma transacción se marcan hilos como leídos, vale lo último.
        send_to_group(
            group,
            {'type': handler, 'payload': {**payload, 'total_unread': clinic_unread_total(clinic_id)}},
        )

    transaction.on_commit(send)


def broadcast_message(message):
    """Hay un mensaje nuevo en un hilo, o ha cambiado su estado de entrega."""
    session = message.session
    if session.is_test or not message.clinic_id:
        return
    _emit(
        message.clinic_id,
        'chat.message',
        {
            'type': 'message',
            'session_id': str(session.id),
            'message_id': str(message.id),
            'direction': message.direction,
            'status': message.status,
            'unread_count': session.unread_count,
            'last_message_at': _isoformat(session.last_message_at),
        },
    )


def broadcast_session(session):
    """Ha cambiado la cabecera de un hilo: no leídos, modo del agente…"""
    if session.is_test or not session.clinic_id:
        return
    _emit(
        session.clinic_id,
        'chat.session',
        {
            'type': 'session',
            'session_id': str(session.id),
            'unread_count': session.unread_count,
            'agent_paused': session.agent_paused,
            'last_message_at': _isoformat(session.last_message_at),
        },
    )


def broadcast_clinic(clinic):
    """Ha cambiado el interruptor general del agente de la clínica.

    Un único aviso en vez de uno por hilo: no cambia ningún hilo, cambia la
    clínica, y la bandeja entera tiene que repintarse igual.
    """
    _emit(
        clinic.clinic_id,
        'chat.clinic',
        {'type': 'clinic', 'agent_enabled': clinic.agent_enabled},
    )
