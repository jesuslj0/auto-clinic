"""Acuses de entrega de WhatsApp (✓, ✓✓, leído, fallido) y contador en móvil."""
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from agent.models import ChatMessage, ConversationSession
from agent.services import apply_delivery_status, record_message
from audit.models import ChangeLog

URL = '/api/agent/messages/status/'


@pytest.fixture
def session_a(db, clinic_a):
    return ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')


@pytest.fixture
def outbound(session_a):
    return record_message(
        clinic=session_a.clinic, session=session_a, direction=ChatMessage.Direction.OUTBOUND,
        sender=ChatMessage.Sender.STAFF, body='Mañana a las 10', status=ChatMessage.Status.SENT,
        wa_message_id='wamid.OUT1',
    )


@pytest.fixture
def agent_client(clinic_a):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return client


def _post(client, payload):
    return client.post(URL, payload, format='json')


def _reload(message):
    message.refresh_from_db()
    return message


@pytest.mark.django_db
class TestStatusProgression:
    def test_delivered_then_read(self, agent_client, outbound):
        assert _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered', 'timestamp': '1790000000'}).status_code == 200
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'read', 'timestamp': '1790000060'})
        message = _reload(outbound)
        assert message.status == 'read'
        assert message.delivered_at == datetime.fromtimestamp(1790000000, tz=dt_timezone.utc)
        assert message.seen_at == datetime.fromtimestamp(1790000060, tz=dt_timezone.utc)

    def test_out_of_order_never_goes_back(self, agent_client, outbound):
        # Meta no garantiza el orden: el «read» puede llegar antes.
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'read', 'timestamp': '1790000060'})
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered', 'timestamp': '1790000000'})
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'sent', 'timestamp': '1789999990'})
        message = _reload(outbound)
        assert message.status == 'read'
        # «read» implica entregado: se selló con la primera marca que llegó.
        assert message.delivered_at == datetime.fromtimestamp(1790000060, tz=dt_timezone.utc)

    def test_repeated_status_is_idempotent(self, agent_client, outbound, django_capture_on_commit_callbacks):
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered'})
        before = ChangeLog.objects.filter(object_id=str(outbound.pk)).count()
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered'})
        assert send.call_count == 0
        assert ChangeLog.objects.filter(object_id=str(outbound.pk)).count() == before

    def test_failed_keeps_the_reason(self, agent_client, outbound):
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'failed', 'error': '131047: Re-engagement message'})
        message = _reload(outbound)
        assert message.status == 'failed'
        assert '131047' in message.error_message

    def test_failed_after_delivery_is_ignored(self, agent_client, outbound):
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered'})
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'failed', 'error': 'x'})
        assert _reload(outbound).status == 'delivered'

    def test_iso_timestamp_is_accepted(self, agent_client, outbound):
        _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'delivered', 'timestamp': '2026-09-27T10:00:00Z'})
        assert _reload(outbound).delivered_at == datetime(2026, 9, 27, 10, 0, tzinfo=dt_timezone.utc)

    def test_list_of_statuses(self, agent_client, outbound):
        response = _post(agent_client, [
            {'wa_message_id': 'wamid.OUT1', 'status': 'delivered'},
            {'wa_message_id': 'wamid.NADIE', 'status': 'read'},
        ])
        assert response.status_code == 200
        assert [r['matched'] for r in response.data] == [True, False]


@pytest.mark.django_db
class TestStatusScoping:
    def test_unknown_message_is_not_an_error(self, agent_client, outbound):
        response = _post(agent_client, {'wa_message_id': 'wamid.NADIE', 'status': 'read'})
        assert response.status_code == 200
        assert response.data == {'wa_message_id': 'wamid.NADIE', 'matched': False, 'status': None}

    def test_other_clinic_cannot_touch_it(self, clinic_b, outbound):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_b.agent_api_key}')
        response = _post(client, {'wa_message_id': 'wamid.OUT1', 'status': 'read'})
        assert response.data['matched'] is False
        assert _reload(outbound).status == 'sent'

    def test_inbound_messages_have_no_delivery_status(self, agent_client, session_a):
        inbound = record_message(
            clinic=session_a.clinic, session=session_a, direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT, body='hola', wa_message_id='wamid.IN1',
        )
        assert _post(agent_client, {'wa_message_id': 'wamid.IN1', 'status': 'read'}).data['matched'] is False
        assert _reload(inbound).status == ''

    def test_invalid_status_is_400(self, agent_client, outbound):
        assert _post(agent_client, {'wa_message_id': 'wamid.OUT1', 'status': 'queued'}).status_code == 400

    def test_staff_cannot_use_it(self, staff_user, outbound):
        client = APIClient()
        client.force_authenticate(staff_user)
        assert _post(client, {'wa_message_id': 'wamid.OUT1', 'status': 'read'}).status_code == 403


@pytest.mark.django_db
class TestStatusSideEffects:
    def test_change_is_announced_after_commit(self, clinic_a, outbound, django_capture_on_commit_callbacks):
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                apply_delivery_status(clinic=clinic_a, wa_message_id='wamid.OUT1', status='read')
        (_, event), = [c.args for c in send.call_args_list]
        assert event['payload']['message_id'] == str(outbound.pk)
        assert event['payload']['status'] == 'read'

    def test_change_is_audited(self, clinic_a, outbound):
        apply_delivery_status(clinic=clinic_a, wa_message_id='wamid.OUT1', status='read')
        entry = ChangeLog.objects.filter(object_id=str(outbound.pk), action='update').latest('timestamp')
        assert entry.changes['status'] == {'before': 'sent', 'after': 'read'}


@pytest.mark.django_db
class TestStatusRendering:
    def _bubble(self, client, staff_user, message):
        client.force_login(staff_user)
        url = reverse('agent:chat-messages-fragment', args=[message.session_id])
        return client.get(url, {'only': str(message.pk)}).content.decode()

    def test_read_shows_blue_double_tick_with_times(self, client, staff_user, clinic_a, outbound):
        apply_delivery_status(clinic=clinic_a, wa_message_id='wamid.OUT1', status='read')
        html = self._bubble(client, staff_user, outbound)
        assert 'data-status="read"' in html
        assert 'text-sky-300' in html
        assert 'leído' in html

    def test_failed_says_so_and_why(self, client, staff_user, clinic_a, outbound):
        apply_delivery_status(clinic=clinic_a, wa_message_id='wamid.OUT1', status='failed', error='Número no válido')
        html = self._bubble(client, staff_user, outbound)
        assert 'No enviado' in html
        assert 'Número no válido' in html


@pytest.mark.django_db
def test_mobile_menu_button_carries_the_unread_count(client, staff_user, session_a):
    record_message(
        clinic=session_a.clinic, session=session_a, direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT, body='¿Hay hueco?',
    )
    client.force_login(staff_user)
    html = client.get(reverse('appointments:calendar')).content.decode()
    button = html[html.index('aria-label="Abrir menú"'):]
    button = button[: button.index('</button>')]
    assert 'data-chat-unread-count>1<' in button


@pytest.mark.django_db
def test_test_thread_bubbles_show_no_delivery_ticks(clinic_a):
    from django.template.loader import render_to_string

    session = ConversationSession.objects.create(clinic=clinic_a, phone='+34600000002', is_test=True)
    message = record_message(
        clinic=clinic_a, session=session, direction=ChatMessage.Direction.OUTBOUND,
        sender=ChatMessage.Sender.AGENT, body='hola', status=ChatMessage.Status.SENT,
    )
    assert 'data-status' not in render_to_string('agent/_message_bubble.html', {'message': message})

    session.is_test = False
    assert 'data-status="sent"' in render_to_string('agent/_message_bubble.html', {'message': message})
