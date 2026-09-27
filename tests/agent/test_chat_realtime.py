"""Tiempo real del panel de chats (agent.consumers, agent.realtime)."""
from unittest.mock import patch

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.test import Client
from django.urls import reverse

from agent.consumers import CHATS_STREAM, ChatConsumer
from agent.models import ChatMessage, ConversationSession
from agent.services import mark_session_read, record_message
from config.asgi import application
from core.consumers import CLOSE_NO_CLINIC, CLOSE_UNAUTHENTICATED
from core.realtime import clinic_group_name


def _event(payload):
    return {'type': 'chat.message', 'payload': payload}


async def _connect(user):
    communicator = WebsocketCommunicator(ChatConsumer.as_asgi(), '/ws/chats/')
    communicator.scope['user'] = user
    connected, _ = await communicator.connect()
    return communicator, connected


async def _close_code(communicator):
    output = await communicator.receive_output(timeout=1)
    assert output['type'] == 'websocket.close'
    return output['code']


# ---------------------------------------------------------------------------
# Nombre del grupo
# ---------------------------------------------------------------------------

class TestClinicGroupName:
    def test_is_valid_for_channels_whatever_the_clinic_id(self):
        import re

        for clinic_id in ['clinic-alpha', 'Clínica Gaena / Madrid', 'x' * 100]:
            name = clinic_group_name(CHATS_STREAM, clinic_id)
            assert re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', name)

    def test_differs_between_clinics_and_streams(self):
        assert clinic_group_name('chats', 'a') != clinic_group_name('chats', 'b')
        assert clinic_group_name('chats', 'a') != clinic_group_name('appointments', 'a')


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------

@pytest.fixture
def staff_session_cookie(staff_user):
    # Síncrono a propósito: `force_login` toca la base de datos y no puede
    # llamarse desde dentro del test async.
    client = Client()
    client.force_login(staff_user)
    return f"sessionid={client.cookies['sessionid'].value}".encode()


@pytest.mark.django_db(transaction=True)
class TestChatConsumerAccess:
    async def test_anonymous_is_closed_with_4401(self):
        communicator, _ = await _connect(AnonymousUser())
        assert await _close_code(communicator) == CLOSE_UNAUTHENTICATED

    async def test_inactive_user_is_closed_with_4401(self, staff_user):
        staff_user.is_active = False
        communicator, _ = await _connect(staff_user)
        assert await _close_code(communicator) == CLOSE_UNAUTHENTICATED

    async def test_user_without_clinic_is_closed_with_4403(self, superuser):
        communicator, _ = await _connect(superuser)
        assert await _close_code(communicator) == CLOSE_NO_CLINIC

    async def test_route_reads_the_session_cookie(self, staff_session_cookie, clinic_a):
        # De extremo a extremo por `config.asgi`: la ruta existe y el usuario
        # sale de la cookie de sesión, no de nada que mande el cliente.
        communicator = WebsocketCommunicator(
            application, '/ws/chats/', headers=[(b'cookie', staff_session_cookie)]
        )
        connected, _ = await communicator.connect()
        assert connected is True

        await get_channel_layer().group_send(
            clinic_group_name(CHATS_STREAM, clinic_a.clinic_id), _event({'type': 'message'})
        )
        assert (await communicator.receive_json_from(timeout=3))['type'] == 'message'
        await communicator.disconnect()

    async def test_route_without_cookie_is_closed_with_4401(self):
        communicator = WebsocketCommunicator(application, '/ws/chats/')
        await communicator.connect()
        assert await _close_code(communicator) == CLOSE_UNAUTHENTICATED


@pytest.mark.django_db(transaction=True)
class TestChatConsumerIsolation:
    async def test_clinic_a_does_not_receive_clinic_b_events(self, staff_user, admin_user_b):
        comm_a, _ = await _connect(staff_user)
        comm_b, _ = await _connect(admin_user_b)

        await get_channel_layer().group_send(
            clinic_group_name(CHATS_STREAM, admin_user_b.clinic_id), _event({'type': 'message'})
        )

        assert (await comm_b.receive_json_from(timeout=3))['type'] == 'message'
        assert await comm_a.receive_nothing(timeout=0.5) is True
        await comm_a.disconnect()
        await comm_b.disconnect()

    async def test_client_messages_are_ignored(self, staff_user):
        communicator, _ = await _connect(staff_user)
        await communicator.send_to(text_data='{"type": "message", "body": "hola"}')
        assert await communicator.receive_nothing(timeout=0.5) is True
        await communicator.disconnect()


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------

@pytest.fixture
def session_a(db, clinic_a):
    return ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')


def _record_inbound(session, body='Hola'):
    return record_message(
        clinic=session.clinic,
        session=session,
        direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT,
        body=body,
    )


@pytest.mark.django_db
class TestBroadcastFromServices:
    def test_record_message_emits_only_after_commit(self, session_a, django_capture_on_commit_callbacks):
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                with transaction.atomic():
                    _record_inbound(session_a)
                    assert send.call_count == 0
            assert send.call_count == 2

        group = clinic_group_name(CHATS_STREAM, session_a.clinic_id)
        (msg_group, msg_event), (sess_group, sess_event) = [c.args for c in send.call_args_list]
        assert msg_group == sess_group == group
        assert msg_event['type'] == 'chat.message'
        assert msg_event['payload']['unread_count'] == 1
        assert sess_event['type'] == 'chat.session'

    def test_payload_does_not_carry_the_message_body(self, session_a, django_capture_on_commit_callbacks):
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                _record_inbound(session_a, body='Me duele mucho el juanete')

        for call in send.call_args_list:
            assert 'juanete' not in str(call.args[1])

    def test_duplicate_wa_message_id_does_not_emit_again(self, session_a, django_capture_on_commit_callbacks):
        kwargs = dict(
            clinic=session_a.clinic, session=session_a, direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT, body='Hola', wa_message_id='wamid.1',
        )
        record_message(**kwargs)
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                record_message(**kwargs)
        assert send.call_count == 0

    def test_test_sessions_are_not_emitted(self, clinic_a, django_capture_on_commit_callbacks):
        session = ConversationSession.objects.create(clinic=clinic_a, phone='panel-test', is_test=True)
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                _record_inbound(session)
        assert send.call_count == 0

    def test_mark_session_read_emits_zero_unread(self, session_a, django_capture_on_commit_callbacks):
        _record_inbound(session_a)
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                mark_session_read(session_a)
        (_, event), = [c.args for c in send.call_args_list]
        assert event['payload']['unread_count'] == 0

    def test_channel_layer_failure_does_not_break_ingestion(self, session_a, django_capture_on_commit_callbacks):
        with patch('core.realtime.async_to_sync', side_effect=ConnectionError('redis caído')):
            with django_capture_on_commit_callbacks(execute=True):
                message = _record_inbound(session_a)
        assert ChatMessage.objects.filter(pk=message.pk).exists()


@pytest.mark.django_db
class TestBroadcastFromViews:
    def test_toggle_agent_emits_session(self, client, staff_user, session_a, django_capture_on_commit_callbacks):
        client.force_login(staff_user)
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                client.post(reverse('agent:chat-toggle-agent', args=[session_a.id]))
        (_, event), = [c.args for c in send.call_args_list]
        assert event['type'] == 'chat.session'
        assert event['payload']['agent_paused'] is True

    def test_clinic_switch_emits_a_single_clinic_event(
        self, client, admin_user, session_a, django_capture_on_commit_callbacks
    ):
        client.force_login(admin_user)
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                client.post(reverse('agent:agent-switch'))
        (_, event), = [c.args for c in send.call_args_list]
        assert event['type'] == 'chat.clinic'


@pytest.mark.django_db
class TestBroadcastFromSessionApi:
    def test_patch_agent_paused_emits(self, admin_client, session_a, django_capture_on_commit_callbacks):
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                response = admin_client.patch(
                    f'/api/agent/sessions/{session_a.pk}/', {'agent_paused': True}, format='json'
                )
        assert response.status_code == 200
        (_, event), = [c.args for c in send.call_args_list]
        assert event['payload']['agent_paused'] is True

    def test_patch_last_interaction_does_not_emit(
        self, admin_client, session_a, django_capture_on_commit_callbacks
    ):
        # Es lo que hace el orquestador de n8n dos veces por mensaje; el aviso
        # de ese mensaje ya lo emitió `record_message()`.
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                response = admin_client.patch(
                    f'/api/agent/sessions/{session_a.pk}/',
                    {'last_interaction': '2026-09-24T10:00:00Z', 'session_data': {'step': 'x'}},
                    format='json',
                )
        assert response.status_code == 200
        assert send.call_count == 0


@pytest.mark.django_db
def test_messages_have_no_bulk_create(admin_client, session_a):
    # Se saltaría `record_message()`: sin deduplicar, sin cabecera y sin aviso.
    payload = [{'session': str(session_a.pk), 'direction': 'inbound', 'sender': 'patient', 'body': 'x'}]
    response = admin_client.post('/api/agent/messages/bulk-create/', payload, format='json')
    assert response.status_code in (404, 405)


@pytest.mark.django_db
class TestTotalUnreadInEvents:
    def test_events_carry_the_clinic_total(self, clinic_a, session_a, django_capture_on_commit_callbacks):
        other = ConversationSession.objects.create(clinic=clinic_a, phone='+34600333444')
        _record_inbound(other)
        test_thread = ConversationSession.objects.create(clinic=clinic_a, phone='panel-test', is_test=True)
        _record_inbound(test_thread)  # no cuenta: la bandeja no lo muestra

        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                _record_inbound(session_a)
        for call in send.call_args_list:
            assert call.args[1]['payload']['total_unread'] == 2

    def test_total_is_counted_after_commit(self, session_a, django_capture_on_commit_callbacks):
        # Mensaje y lectura en la misma transacción: el total que sale es el final.
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                with transaction.atomic():
                    _record_inbound(session_a)
                    mark_session_read(session_a)
        assert {c.args[1]['payload']['total_unread'] for c in send.call_args_list} == {0}
