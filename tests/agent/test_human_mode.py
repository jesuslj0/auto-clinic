"""Modo humano: envío desde el panel, pausa del agente y reactivación automática."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message, send_staff_message
from agent.whatsapp import WhatsAppError


@pytest.fixture
def active_session(db, clinic_a, patient_a):
    """Conversación con un mensaje entrante reciente: dentro de la ventana de 24 h."""
    session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone)
    record_message(
        clinic=clinic_a,
        session=session,
        direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT,
        body="¿Me podéis confirmar la cita?",
    )
    session.refresh_from_db()
    return session


@pytest.fixture
def session_other_clinic(db, clinic_b):
    return ConversationSession.objects.create(clinic=clinic_b, phone="+34699999999")


@pytest.mark.django_db
class TestAgentShouldReply:
    """La decisión que consulta n8n antes de generar una respuesta."""

    def test_replies_by_default(self, active_session):
        assert active_session.agent_should_reply is True

    def test_silent_when_clinic_switch_is_off(self, active_session, clinic_a):
        clinic_a.agent_enabled = False
        clinic_a.save(update_fields=['agent_enabled'])
        active_session.refresh_from_db()
        assert active_session.agent_should_reply is False

    def test_silent_when_thread_is_paused(self, active_session):
        active_session.agent_paused = True
        assert active_session.agent_should_reply is False

    def test_silent_right_after_a_staff_message(self, active_session):
        active_session.last_staff_message_at = timezone.now()
        assert active_session.is_handoff_active is True
        assert active_session.agent_should_reply is False

    def test_resumes_once_the_handoff_window_expires(self, active_session, clinic_a):
        active_session.last_staff_message_at = timezone.now() - timedelta(
            seconds=clinic_a.agent_handoff_timeout_seconds + 1
        )
        assert active_session.is_handoff_active is False
        assert active_session.agent_should_reply is True

    def test_zero_timeout_disables_automatic_resume(self, active_session, clinic_a):
        """Con el plazo a cero la pausa por escribir deja de aplicarse."""
        clinic_a.agent_handoff_timeout_seconds = 0
        clinic_a.save(update_fields=['agent_handoff_timeout_seconds'])
        active_session.refresh_from_db()
        active_session.last_staff_message_at = timezone.now()
        assert active_session.is_handoff_active is False


@pytest.mark.django_db
class TestCustomerServiceWindow:
    def test_open_within_24h(self, active_session):
        assert active_session.can_send_free_text is True

    def test_closed_after_24h(self, active_session):
        active_session.last_interaction = timezone.now() - timedelta(hours=24, minutes=1)
        assert active_session.can_send_free_text is False

    def test_closed_when_patient_never_wrote(self, db, clinic_a):
        session = ConversationSession.objects.create(clinic=clinic_a, phone="+34600111222")
        assert session.can_send_free_text is False


@pytest.mark.django_db
class TestSendStaffMessage:
    def test_records_message_and_pauses_agent(self, active_session):
        with patch('agent.services.send_text', return_value='wamid.TEST') as send:
            message = send_staff_message(session=active_session, body='Confirmada para el jueves')

        send.assert_called_once()
        assert message.sender == ChatMessage.Sender.STAFF
        assert message.direction == ChatMessage.Direction.OUTBOUND
        assert message.status == ChatMessage.Status.SENT
        assert message.wa_message_id == 'wamid.TEST'

        active_session.refresh_from_db()
        assert active_session.last_staff_message_at is not None
        # Escribir aparta al agente sin necesidad de tocar el interruptor.
        assert active_session.agent_should_reply is False

    def test_failed_send_is_kept_as_failed(self, active_session):
        with patch('agent.services.send_text', side_effect=WhatsAppError('Token caducado')):
            with pytest.raises(WhatsAppError):
                send_staff_message(session=active_session, body='Hola')

        message = active_session.messages.filter(sender=ChatMessage.Sender.STAFF).get()
        assert message.status == ChatMessage.Status.FAILED
        assert 'Token caducado' in message.error_message

    def test_refuses_outside_the_24h_window(self, active_session):
        active_session.last_interaction = timezone.now() - timedelta(hours=25)
        active_session.save(update_fields=['last_interaction'])

        with patch('agent.services.send_text') as send:
            with pytest.raises(WhatsAppError, match='24 horas'):
                send_staff_message(session=active_session, body='Hola')

        # Ni se llama a WhatsApp ni queda una burbuja fallida en el hilo.
        send.assert_not_called()
        assert not active_session.messages.filter(sender=ChatMessage.Sender.STAFF).exists()

    def test_rejects_empty_body(self, active_session):
        with pytest.raises(ValueError):
            send_staff_message(session=active_session, body='   ')


@pytest.mark.django_db
class TestChatSendView:
    def test_requires_login(self, client, active_session):
        response = client.post(
            reverse('agent:chat-send', args=[active_session.id]), {'body': 'Hola'}
        )
        assert response.status_code == 302
        assert '/login' in response.url

    def test_staff_can_send(self, client, staff_user, active_session):
        client.force_login(staff_user)
        with patch('agent.services.send_text', return_value='wamid.OK'):
            response = client.post(
                reverse('agent:chat-send', args=[active_session.id]), {'body': 'Vale'}
            )
        assert response.status_code == 302
        assert active_session.messages.filter(sender=ChatMessage.Sender.STAFF).count() == 1

    def test_cannot_send_to_another_clinic(self, client, staff_user, session_other_clinic):
        client.force_login(staff_user)
        with patch('agent.services.send_text') as send:
            response = client.post(
                reverse('agent:chat-send', args=[session_other_clinic.id]), {'body': 'Hola'}
            )
        assert response.status_code == 404
        send.assert_not_called()


@pytest.mark.django_db
class TestToggleViews:
    def test_toggle_thread_mode(self, client, staff_user, active_session):
        client.force_login(staff_user)
        url = reverse('agent:chat-toggle-agent', args=[active_session.id])

        client.post(url)
        active_session.refresh_from_db()
        assert active_session.agent_paused is True

        client.post(url)
        active_session.refresh_from_db()
        assert active_session.agent_paused is False

    def test_explicit_pause_clears_the_temporary_one(self, client, staff_user, active_session):
        """Poner el hilo en modo humano no debe caducar por inactividad."""
        active_session.last_staff_message_at = timezone.now()
        active_session.save(update_fields=['last_staff_message_at'])

        client.force_login(staff_user)
        client.post(reverse('agent:chat-toggle-agent', args=[active_session.id]))

        active_session.refresh_from_db()
        assert active_session.agent_paused is True
        assert active_session.last_staff_message_at is None

    def test_cannot_toggle_another_clinic(self, client, staff_user, session_other_clinic):
        client.force_login(staff_user)
        response = client.post(
            reverse('agent:chat-toggle-agent', args=[session_other_clinic.id])
        )
        assert response.status_code == 404

    def test_clinic_switch(self, client, staff_user, clinic_a):
        client.force_login(staff_user)
        client.post(reverse('agent:agent-switch'))
        clinic_a.refresh_from_db()
        assert clinic_a.agent_enabled is False

    def test_clinic_switch_ignores_external_redirect(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(reverse('agent:agent-switch'), {'next': 'https://evil.example/x'})
        assert response.status_code == 302
        assert response.url == reverse('agent:chat-inbox')


@pytest.mark.django_db
class TestShouldReplyEndpoint:
    """El endpoint que consulta n8n: decisión + contexto para retomar el hilo."""

    url = '/api/agent/sessions/should-reply/'

    def test_returns_decision_and_history(self, staff_client, active_session):
        response = staff_client.get(self.url, {'phone': active_session.phone})
        assert response.status_code == 200
        assert response.data['agent_should_reply'] is True
        assert response.data['session_id'] == str(active_session.id)
        assert len(response.data['history']) == 1

    def test_history_includes_staff_messages(self, staff_client, active_session):
        """Lo que escribió la recepcionista tiene que llegarle al agente.

        Su memoria en n8n no lo contiene: ese mensaje salió por Django.
        """
        with patch('agent.services.send_text', return_value='wamid.X'):
            send_staff_message(session=active_session, body='Te confirmo el jueves')

        response = staff_client.get(self.url, {'phone': active_session.phone})
        senders = [item['sender'] for item in response.data['history']]
        assert ChatMessage.Sender.STAFF in senders
        assert response.data['agent_should_reply'] is False

    def test_phone_is_required(self, staff_client):
        assert staff_client.get(self.url).status_code == 400

    def test_unknown_phone_defers_to_clinic_switch(self, staff_client, clinic_a):
        response = staff_client.get(self.url, {'phone': '+34600000000'})
        assert response.status_code == 200
        assert response.data['session_id'] is None
        assert response.data['agent_should_reply'] is True

    def test_does_not_expose_another_clinic(self, staff_client, session_other_clinic):
        response = staff_client.get(self.url, {'phone': session_other_clinic.phone})
        assert response.status_code == 200
        assert response.data['session_id'] is None
