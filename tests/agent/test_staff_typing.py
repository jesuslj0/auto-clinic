"""«Escribiendo…» en el WhatsApp del paciente mientras el staff redacta.

El compositor llama a `agent:chat-typing`; Django pide a Meta el indicador sobre
el último mensaje del paciente. Es un adorno: nunca falla hacia fuera, no sale
fuera de la ventana de 24 h ni sin un mensaje al que engancharse, y se limita a
una llamada cada `STAFF_TYPING_INTERVAL_SECONDS` por hilo.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message, signal_staff_typing
from agent.whatsapp import WhatsAppError, send_typing


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def whatsapp_clinic(clinic_a):
    clinic_a.whatsapp_phone_number_id = '123456'
    clinic_a.whatsapp_token = 'token-de-prueba'
    clinic_a.save(update_fields=['whatsapp_phone_number_id', 'whatsapp_token'])
    return clinic_a


@pytest.fixture
def session(db, whatsapp_clinic, patient_a):
    session = ConversationSession.objects.create(clinic=whatsapp_clinic, phone=patient_a.phone)
    for wamid, body in (('wamid.PRIMERO', 'Hola'), ('wamid.ULTIMO', '¿Tenéis hueco?')):
        record_message(
            clinic=whatsapp_clinic,
            session=session,
            direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT,
            body=body,
            wa_message_id=wamid,
        )
    session.refresh_from_db()
    return session


def typing_url(session):
    return reverse('agent:chat-typing', kwargs={'session_id': session.id})


@pytest.mark.django_db
class TestSignalStaffTyping:
    def test_sends_on_last_inbound_message(self, session):
        with patch('agent.services.send_typing') as send:
            assert signal_staff_typing(session) is True
        send.assert_called_once_with(session.clinic, 'wamid.ULTIMO')

    def test_throttled_per_thread(self, session):
        with patch('agent.services.send_typing') as send:
            assert signal_staff_typing(session) is True
            assert signal_staff_typing(session) is False
        assert send.call_count == 1

    def test_not_sent_outside_24h_window(self, session):
        session.last_interaction = timezone.now() - timedelta(hours=25)
        with patch('agent.services.send_typing') as send:
            assert signal_staff_typing(session) is False
        send.assert_not_called()

    def test_not_sent_without_inbound_whatsapp_id(self, whatsapp_clinic, patient_a):
        session = ConversationSession.objects.create(clinic=whatsapp_clinic, phone=patient_a.phone)
        record_message(
            clinic=whatsapp_clinic,
            session=session,
            direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT,
            body='Sin id de WhatsApp',
        )
        session.refresh_from_db()
        with patch('agent.services.send_typing') as send:
            assert signal_staff_typing(session) is False
        send.assert_not_called()

    def test_meta_error_is_swallowed(self, session):
        with patch('agent.services.send_typing', side_effect=WhatsAppError('Token caducado')):
            assert signal_staff_typing(session) is False


@pytest.mark.django_db
class TestSendTypingPayload:
    def test_marks_read_and_shows_typing(self, whatsapp_clinic):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"success": true}'
        with patch('agent.whatsapp.urllib.request.urlopen', return_value=response) as urlopen:
            send_typing(whatsapp_clinic, 'wamid.X')

        request = urlopen.call_args.args[0]
        assert request.full_url.endswith('/123456/messages')
        assert request.headers['Authorization'] == 'Bearer token-de-prueba'
        assert json.loads(request.data) == {
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': 'wamid.X',
            'typing_indicator': {'type': 'text'},
        }


@pytest.mark.django_db
class TestTypingView:
    def test_staff_of_the_clinic(self, client, admin_user, session):
        client.force_login(admin_user)
        with patch('agent.services.send_typing') as send:
            response = client.post(typing_url(session))
        assert response.status_code == 204
        send.assert_called_once()

    def test_always_204_even_if_nothing_was_sent(self, client, admin_user, session):
        client.force_login(admin_user)
        with patch('agent.services.send_typing', side_effect=WhatsAppError('caído')):
            assert client.post(typing_url(session)).status_code == 204

    def test_other_clinic_gets_404(self, client, admin_user_b, session):
        client.force_login(admin_user_b)
        with patch('agent.services.send_typing') as send:
            assert client.post(typing_url(session)).status_code == 404
        send.assert_not_called()

    def test_anonymous_is_denied(self, client, session):
        with patch('agent.services.send_typing') as send:
            assert client.post(typing_url(session)).status_code == 403
        send.assert_not_called()

    def test_get_not_allowed(self, client, admin_user, session):
        client.force_login(admin_user)
        assert client.get(typing_url(session)).status_code == 405

    def test_composer_carries_typing_url(self, client, admin_user, session):
        client.force_login(admin_user)
        response = client.get(reverse('agent:chat-thread', kwargs={'session_id': session.id}))
        assert f'data-typing-url="{typing_url(session)}"' in response.content.decode()
