"""Fragmentos HTML del panel de chats que el cliente pide en vivo."""
from datetime import timedelta

import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message
from patients.templatetags.patient_extras import split_phone


def _inbound(session, body):
    return record_message(
        clinic=session.clinic,
        session=session,
        direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT,
        body=body,
    )


def _ids(response):
    import re

    return re.findall(r'data-message-id="([0-9a-f-]+)"', response.content.decode())


@pytest.fixture
def session_a(db, clinic_a):
    return ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')


@pytest.fixture
def thread_a(session_a):
    return [_inbound(session_a, f'mensaje {i}') for i in range(5)]


@pytest.fixture
def session_b(db, clinic_b):
    session = ConversationSession.objects.create(clinic=clinic_b, phone='+34699999999')
    _inbound(session, 'de otra clínica')
    return session


def _messages_url(session, **params):
    return reverse('agent:chat-messages-fragment', args=[session.id]), params


@pytest.mark.django_db
class TestMessagesFragment:
    def test_after_returns_only_later_messages_in_order(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(thread_a[1].id))
        response = client.get(url, params)
        assert response.status_code == 200
        assert _ids(response) == [str(m.id) for m in thread_a[2:]]
        assert response['X-Has-More'] == '0'

    def test_after_last_message_is_empty(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(thread_a[-1].id))
        response = client.get(url, params)
        assert response.status_code == 200
        assert _ids(response) == []

    def test_without_after_returns_latest(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        url, _ = _messages_url(session_a)
        assert _ids(client.get(url)) == [str(m.id) for m in thread_a]

    def test_big_gap_is_paginated_with_has_more(self, client, staff_user, session_a, thread_a, monkeypatch):
        from agent.views import ChatMessagesFragmentView

        monkeypatch.setattr(ChatMessagesFragmentView, 'max_batch', 2)
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(thread_a[0].id))
        response = client.get(url, params)
        assert _ids(response) == [str(m.id) for m in thread_a[1:3]]
        assert response['X-Has-More'] == '1'

        # El cliente repite con el último que ha recibido y completa el hueco.
        response = client.get(url, {'after': str(thread_a[2].id)})
        assert _ids(response) == [str(m.id) for m in thread_a[3:]]
        assert response['X-Has-More'] == '0'

    def test_same_timestamp_is_not_lost(self, client, staff_user, session_a, thread_a):
        # Dos mensajes en el mismo instante: el desempate por id no pierde ninguno.
        ChatMessage.objects.filter(pk__in=[m.pk for m in thread_a]).update(created_at=thread_a[0].created_at)
        ordered = sorted(thread_a, key=lambda m: str(m.id))
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(ordered[1].id))
        assert _ids(client.get(url, params)) == [str(m.id) for m in ordered[2:]]

    def test_after_from_another_thread_is_400(self, client, staff_user, clinic_a, session_a, thread_a):
        other = ConversationSession.objects.create(clinic=clinic_a, phone='+34600999888')
        foreign = _inbound(other, 'otro hilo')
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(foreign.id))
        assert client.get(url, params).status_code == 400

    def test_malformed_after_is_400(self, client, staff_user, session_a):
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after='no-es-un-uuid')
        assert client.get(url, params).status_code == 400

    def test_marks_thread_as_read(self, client, staff_user, session_a, thread_a):
        session_a.refresh_from_db()
        assert session_a.unread_count == 5
        client.force_login(staff_user)
        url, _ = _messages_url(session_a)
        client.get(url)
        session_a.refresh_from_db()
        assert session_a.unread_count == 0

    def test_day_divider_only_when_the_day_changes(self, client, staff_user, session_a, thread_a):
        # Todo es de hoy y el navegador ya tiene el primero: sin separador.
        client.force_login(staff_user)
        url, params = _messages_url(session_a, after=str(thread_a[0].id))
        assert 'data-day=' not in client.get(url, params).content.decode()

        # Si el de referencia es de ayer, lo nuevo abre día.
        ChatMessage.objects.filter(pk=thread_a[0].pk).update(created_at=thread_a[0].created_at - timedelta(days=1))
        assert client.get(url, params).content.decode().count('data-day=') == 1

    def test_other_clinic_thread_is_404(self, client, staff_user, session_b):
        client.force_login(staff_user)
        url, _ = _messages_url(session_b)
        assert client.get(url).status_code == 404

    def test_test_thread_is_404(self, client, staff_user, clinic_a):
        session = ConversationSession.objects.create(clinic=clinic_a, phone='panel-test', is_test=True)
        client.force_login(staff_user)
        url, _ = _messages_url(session)
        assert client.get(url).status_code == 404

    def test_anonymous_gets_403_not_the_login_page(self, client, session_a):
        url, _ = _messages_url(session_a)
        assert client.get(url).status_code == 403


@pytest.mark.django_db
class TestSessionListFragment:
    url = staticmethod(lambda: reverse('agent:chat-list-fragment'))

    def test_lists_only_own_clinic(self, client, staff_user, session_a, thread_a, session_b):
        client.force_login(staff_user)
        html = client.get(self.url()).content.decode()
        assert split_phone(session_a.phone)['number'] in html
        assert split_phone(session_b.phone)['number'] not in html

    def test_respects_filters(self, client, staff_user, session_a, thread_a, clinic_a):
        read = ConversationSession.objects.create(clinic=clinic_a, phone='+34611000222')
        client.force_login(staff_user)
        html = client.get(self.url(), {'unread': '1'}).content.decode()
        assert split_phone(session_a.phone)['number'] in html
        assert split_phone(read.phone)['number'] not in html

    def test_total_unread_header_ignores_filters(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        response = client.get(self.url(), {'q': 'nadie-se-llama-asi'})
        assert response['X-Total-Unread'] == '5'

    def test_active_row_is_highlighted(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        html = client.get(self.url(), {'active': str(session_a.id)}).content.decode()
        assert f'data-session-id="{session_a.id}" aria-current="true"' in html
        assert 'aria-current' not in client.get(self.url()).content.decode()

    def test_malformed_active_is_ignored(self, client, staff_user, session_a):
        client.force_login(staff_user)
        assert client.get(self.url(), {'active': 'x'}).status_code == 200

    def test_does_not_mark_anything_as_read(self, client, staff_user, session_a, thread_a):
        client.force_login(staff_user)
        client.get(self.url())
        session_a.refresh_from_db()
        assert session_a.unread_count == 5

    def test_anonymous_gets_403(self, client):
        assert client.get(self.url()).status_code == 403
