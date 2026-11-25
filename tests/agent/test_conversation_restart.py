"""Una conversación nueva (aviso de IA otra vez) empieza cuando el paciente escribe
con la ventana de 24 h cerrada; dentro de la ventana no se reinicia."""
from datetime import timedelta

import pytest
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message

INBOUND = ChatMessage.Direction.INBOUND
PATIENT = ChatMessage.Sender.PATIENT


def _inbound(clinic, session, body='hola'):
    record_message(clinic=clinic, session=session, direction=INBOUND, sender=PATIENT, body=body)
    session.refresh_from_db()


@pytest.fixture
def session(db, clinic_a):
    return ConversationSession.objects.create(
        clinic=clinic_a,
        phone='+34600111222',
        session_data={'last_response': 'Hola, soy el asistente', 'last_message': 'hola', 'otro': 1},
    )


@pytest.mark.django_db
class TestConversationRestart:
    def test_window_closed_starts_a_new_conversation(self, clinic_a, session):
        ConversationSession.objects.filter(pk=session.pk).update(
            last_interaction=timezone.now() - timedelta(hours=25)
        )
        session.refresh_from_db()
        _inbound(clinic_a, session)
        assert 'last_response' not in session.session_data
        assert 'last_message' not in session.session_data
        assert session.session_data['otro'] == 1
        assert session.can_send_free_text

    def test_open_window_keeps_the_conversation(self, clinic_a, session):
        ConversationSession.objects.filter(pk=session.pk).update(
            last_interaction=timezone.now() - timedelta(hours=2)
        )
        session.refresh_from_db()
        _inbound(clinic_a, session)
        assert session.session_data['last_response'] == 'Hola, soy el asistente'

    def test_only_first_message_of_a_burst_restarts(self, clinic_a, session):
        _inbound(clinic_a, session, 'uno')  # sin last_interaction: ventana cerrada
        session.session_data = {**session.session_data, 'last_response': 'respuesta nueva'}
        session.save(update_fields=['session_data'])
        _inbound(clinic_a, session, 'dos')
        assert session.session_data['last_response'] == 'respuesta nueva'

    def test_staff_message_does_not_restart(self, clinic_a, session):
        record_message(
            clinic=clinic_a, session=session, direction=ChatMessage.Direction.OUTBOUND,
            sender=ChatMessage.Sender.STAFF, body='plantilla',
        )
        session.refresh_from_db()
        assert session.session_data['last_response'] == 'Hola, soy el asistente'
