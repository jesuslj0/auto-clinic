"""Panel de chats (agent.views.ChatInboxView)."""
import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message


@pytest.fixture
def session_with_messages(db, clinic_a, patient_a):
    session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone)
    record_message(
        clinic=clinic_a,
        session=session,
        direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT,
        body="Hola, ¿tenéis hueco el martes?",
    )
    record_message(
        clinic=clinic_a,
        session=session,
        direction=ChatMessage.Direction.OUTBOUND,
        sender=ChatMessage.Sender.AGENT,
        body="Sí, a las 10:00 o a las 12:30.",
    )
    session.refresh_from_db()
    return session


@pytest.fixture
def session_other_clinic(db, clinic_b):
    session = ConversationSession.objects.create(clinic=clinic_b, phone="+34699999999")
    record_message(
        clinic=clinic_b,
        session=session,
        direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT,
        body="Mensaje de otra clínica",
    )
    return session


@pytest.mark.django_db
class TestChatInboxAccess:
    def test_requires_login(self, client):
        response = client.get(reverse('agent:chat-inbox'))
        assert response.status_code == 302
        assert '/login' in response.url

    def test_staff_sees_inbox(self, client, staff_user, session_with_messages):
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-inbox'))
        assert response.status_code == 200
        assert session_with_messages.phone in response.content.decode()


@pytest.mark.django_db
class TestChatInboxIsolation:
    def test_hides_other_clinic_sessions(
        self, client, staff_user, session_with_messages, session_other_clinic
    ):
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-inbox'))
        sessions = list(response.context['sessions'])
        assert session_with_messages in sessions
        assert session_other_clinic not in sessions

    def test_other_clinic_thread_returns_404(self, client, staff_user, session_other_clinic):
        client.force_login(staff_user)
        response = client.get(
            reverse('agent:chat-thread', args=[session_other_clinic.id])
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestChatInboxThread:
    def test_shows_messages_in_chronological_order(
        self, client, staff_user, session_with_messages
    ):
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-thread', args=[session_with_messages.id]))
        assert response.status_code == 200
        bodies = [m.body for m in response.context['chat_messages']]
        assert bodies == ["Hola, ¿tenéis hueco el martes?", "Sí, a las 10:00 o a las 12:30."]

    def test_opening_thread_marks_as_read(self, client, staff_user, session_with_messages):
        assert session_with_messages.unread_count == 1
        client.force_login(staff_user)
        client.get(reverse('agent:chat-thread', args=[session_with_messages.id]))
        session_with_messages.refresh_from_db()
        assert session_with_messages.unread_count == 0

    def test_limit_caps_messages_and_flags_older(self, client, staff_user, clinic_a):
        session = ConversationSession.objects.create(clinic=clinic_a, phone="+34600123123")
        for index in range(5):
            record_message(
                clinic=clinic_a,
                session=session,
                direction=ChatMessage.Direction.INBOUND,
                sender=ChatMessage.Sender.PATIENT,
                body=f"mensaje {index}",
            )
        client.force_login(staff_user)
        response = client.get(
            reverse('agent:chat-thread', args=[session.id]), {'limit': 2}
        )
        assert len(response.context['chat_messages']) == 2
        assert response.context['has_older_messages'] is True
        # Se conservan los más recientes, no los primeros.
        assert [m.body for m in response.context['chat_messages']] == ["mensaje 3", "mensaje 4"]

    def test_invalid_limit_falls_back_to_default(self, client, staff_user, session_with_messages):
        client.force_login(staff_user)
        response = client.get(
            reverse('agent:chat-thread', args=[session_with_messages.id]), {'limit': 'muchos'}
        )
        assert response.status_code == 200
        assert len(response.context['chat_messages']) == 2


@pytest.mark.django_db
class TestChatInboxFilters:
    def test_search_by_phone(self, client, staff_user, session_with_messages, clinic_a):
        ConversationSession.objects.create(clinic=clinic_a, phone="+34611000111")
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-inbox'), {'q': session_with_messages.phone})
        sessions = list(response.context['sessions'])
        assert sessions == [session_with_messages]

    def test_unread_filter(self, client, staff_user, session_with_messages, clinic_a):
        read_session = ConversationSession.objects.create(clinic=clinic_a, phone="+34611000222")
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-inbox'), {'unread': '1'})
        sessions = list(response.context['sessions'])
        assert session_with_messages in sessions
        assert read_session not in sessions

    def test_sessions_without_messages_go_last(self, client, staff_user, session_with_messages, clinic_a):
        mute_session = ConversationSession.objects.create(clinic=clinic_a, phone="+34611000333")
        client.force_login(staff_user)
        response = client.get(reverse('agent:chat-inbox'))
        sessions = list(response.context['sessions'])
        assert sessions.index(session_with_messages) < sessions.index(mute_session)
