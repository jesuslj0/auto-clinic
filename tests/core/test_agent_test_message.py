"""El probador del agente (panel → Agente WhatsApp) deja rastro en el historial."""
import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession


@contextmanager
def _n8n_replies(payload):
    """Simula la respuesta del webhook de prueba de n8n."""
    class _Response:
        def read(self):
            return payload.encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch('urllib.request.urlopen', return_value=_Response()):
        yield


@contextmanager
def _n8n_fails():
    error = urllib.error.URLError('sin conexión')
    with patch('urllib.request.urlopen', side_effect=error):
        yield


def _send(client, message="¿Tenéis hueco mañana?"):
    return client.post(
        reverse('core:clinic-agent-test'),
        data=json.dumps({'message': message}),
        content_type='application/json',
    )


@pytest.mark.django_db
class TestAgentTestMessageHistory:
    def test_records_both_sides_of_the_exchange(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies(json.dumps({'reply': 'Sí, a las 10:00'})):
            response = _send(client)

        assert response.status_code == 200
        assert response.json()['reply'] == 'Sí, a las 10:00'

        session = ConversationSession.objects.get(clinic=clinic_a)
        messages = list(session.messages.order_by('created_at'))
        assert [(m.direction, m.sender, m.body) for m in messages] == [
            ('inbound', 'patient', '¿Tenéis hueco mañana?'),
            ('outbound', 'agent', 'Sí, a las 10:00'),
        ]

    def test_marks_the_exchange_as_source_panel_test(self, client, admin_user):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        assert all(m.raw == {'source': 'panel-test'} for m in ChatMessage.objects.all())

    def test_does_not_leave_unread(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.unread_count == 0

    def test_incoming_survives_an_n8n_failure(self, client, admin_user, clinic_a):
        """Si el agente no responde, la pregunta tiene que quedar registrada igual."""
        client.force_login(admin_user)
        with _n8n_fails():
            response = _send(client)

        assert response.status_code == 502
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.messages.count() == 1
        assert session.messages.get().direction == 'inbound'

    def test_empty_reply_is_not_recorded_as_agent_message(self, client, admin_user, clinic_a):
        """No inventamos una burbuja del agente si no dijo nada."""
        client.force_login(admin_user)
        with _n8n_replies(''):
            response = _send(client)

        assert response.json()['reply'] == 'El agente no devolvió ninguna respuesta.'
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.messages.count() == 1

    def test_uses_test_patient_phone_and_links_the_record(
        self, client, admin_user, clinic_a, patient_a
    ):
        clinic_a.test_patient = patient_a
        clinic_a.save(update_fields=['test_patient'])
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)

        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.phone == patient_a.phone
        assert session.patient_id == patient_a.pk

    def test_falls_back_to_panel_test_without_test_patient(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        assert ConversationSession.objects.get(clinic=clinic_a).phone == 'panel-test'

    def test_reuses_the_same_thread_across_messages(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Uno'):
            _send(client, "Primera")
        with _n8n_replies('Dos'):
            _send(client, "Segunda")

        assert ConversationSession.objects.filter(clinic=clinic_a).count() == 1
        assert ChatMessage.objects.count() == 4

    def test_empty_message_records_nothing(self, client, admin_user):
        client.force_login(admin_user)
        response = _send(client, "   ")
        assert response.status_code == 400
        assert ChatMessage.objects.count() == 0
