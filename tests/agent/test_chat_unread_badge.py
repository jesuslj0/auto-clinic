"""Contador de chats sin leer en el menú lateral y script del socket."""
import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message


@pytest.fixture
def unread_session(db, clinic_a):
    session = ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')
    for body in ('uno', 'dos', 'tres'):
        record_message(
            clinic=clinic_a,
            session=session,
            direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT,
            body=body,
        )
    return session


@pytest.mark.django_db
class TestSidebarBadge:
    def test_shows_unread_total_outside_the_inbox(self, client, staff_user, unread_session):
        client.force_login(staff_user)
        response = client.get(reverse('appointments:calendar'))
        assert response.status_code == 200
        assert response.context['chat_unread_total'] == 3
        assert '<span data-chat-unread-count>3</span>' in response.content.decode()

    def test_badge_is_hidden_with_nothing_unread(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse('appointments:calendar')).content.decode()
        assert '<span data-chat-unread-count>0</span>' in html
        badge = html[html.index('data-chat-unread class='):]
        assert ' hidden">' in badge[: badge.index('>') + 1]

    def test_page_loads_the_live_socket(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse('appointments:calendar')).content.decode()
        assert 'js/chat_live.js' in html
        assert 'data-ws-path="/ws/chats/"' in html

    def test_platform_user_without_clinic_gets_no_socket(self, client, superuser):
        client.force_login(superuser)
        response = client.get(reverse('agent:chat-inbox'))
        assert 'js/chat_live.js' not in response.content.decode()
        assert 'chat_unread_total' not in response.context


@pytest.mark.django_db
class TestPollingFallback:
    def test_head_on_the_list_returns_the_total(self, client, staff_user, unread_session):
        # Sin socket, `chat_live.js` saca el total de esta cabecera.
        client.force_login(staff_user)
        response = client.head(reverse('agent:chat-list-fragment'))
        assert response.status_code == 200
        assert response['X-Total-Unread'] == '3'
        assert response.content == b''


@pytest.mark.django_db
class TestInboxLiveHooks:
    def test_inbox_wires_the_component(self, client, staff_user, unread_session):
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-thread', args=[unread_session.id])).content.decode()
        assert 'x-data="chatInbox(' in html
        assert reverse('agent:chat-messages-fragment', args=[unread_session.id]) in html
        assert 'data-session-list' in html
        assert 'js/chat_inbox.js' in html
        assert f'data-direction="inbound"' in html
