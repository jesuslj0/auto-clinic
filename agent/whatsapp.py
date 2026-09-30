"""Envío de mensajes por la WhatsApp Cloud API.

Los mensajes del agente los manda n8n. Este módulo cubre el otro caso: los que
escribe una persona del staff desde el panel de conversaciones, que salen
directamente desde Django con las credenciales de la propia clínica.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings


class WhatsAppError(Exception):
    """Fallo al entregar un mensaje a la Cloud API, con texto listo para enseñar al staff."""


def _require_config(clinic):
    if not clinic.whatsapp_phone_number_id or not clinic.whatsapp_token:
        raise WhatsAppError(
            'Esta clínica no tiene WhatsApp configurado. Completa la conexión en Agente de WhatsApp.'
        )


def _post_message(clinic, payload, *, timeout=20):
    """POST a `/<phone_number_id>/messages` y devuelve el cuerpo de la respuesta.

    Lanza ``WhatsAppError`` con un mensaje en castellano si Meta lo rechaza o no
    se le puede contactar.
    """
    _require_config(clinic)
    version = getattr(settings, 'WHATSAPP_GRAPH_API_VERSION', 'v26.0')
    url = f'https://graph.facebook.com/{version}/{clinic.whatsapp_phone_number_id}/messages'
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {clinic.whatsapp_token}',
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        raise WhatsAppError(_describe_http_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise WhatsAppError('No se pudo contactar con WhatsApp. Revisa la conexión del servidor.') from exc


def send_text(clinic, phone, body):
    """Envía un mensaje de texto y devuelve el ``wamid`` que asigna WhatsApp.

    Lanza ``WhatsAppError`` con un mensaje en castellano si la clínica no está
    configurada o si Meta rechaza el envío.
    """
    raw = _post_message(clinic, {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': phone,
        'type': 'text',
        'text': {'preview_url': False, 'body': body},
    })

    try:
        data = json.loads(raw)
        return data['messages'][0]['id']
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        # El envío pudo salir bien aunque no sepamos leer el id; no es motivo
        # para dar el mensaje por fallido.
        return ''


def send_typing(clinic, wa_message_id):
    """Enseña «escribiendo…» al paciente en respuesta a su mensaje `wa_message_id`.

    Meta lo quita al llegar la respuesta o a los 25 segundos. La misma llamada
    marca ese mensaje como LEÍDO (checks azules): la API no permite una cosa
    sin la otra. Timeout corto: es un adorno y no debe hacer esperar a nadie.
    """
    _post_message(clinic, {
        'messaging_product': 'whatsapp',
        'status': 'read',
        'message_id': wa_message_id,
        'typing_indicator': {'type': 'text'},
    }, timeout=5)


def _describe_http_error(exc):
    detail = ''
    try:
        payload = json.loads(exc.read().decode('utf-8', errors='replace'))
        detail = (payload.get('error') or {}).get('message', '')
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    if exc.code in (401, 403):
        return 'WhatsApp rechazó el token de la clínica. Vuelve a generarlo en Agente de WhatsApp.'
    if exc.code == 400 and 're-engagement' in detail.lower():
        return (
            'Han pasado más de 24 horas desde el último mensaje del paciente, '
            'así que WhatsApp solo permite responder con una plantilla aprobada.'
        )
    if detail:
        return f'WhatsApp devolvió un error: {detail}'
    return f'WhatsApp devolvió el código {exc.code} al enviar el mensaje.'
