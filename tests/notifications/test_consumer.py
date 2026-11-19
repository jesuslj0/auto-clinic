"""NotificationConsumer: solo autenticados con clínica, y solo la suya."""
import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from core.consumers import CLOSE_NO_CLINIC, CLOSE_UNAUTHENTICATED
from core.realtime import clinic_group_name
from notifications.consumers import NOTIFICATIONS_STREAM, NotificationConsumer


async def _connect(user):
    communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), '/ws/notifications/')
    communicator.scope['user'] = user
    await communicator.connect()
    return communicator


@pytest.mark.django_db(transaction=True)
class TestNotificationConsumer:
    async def test_anonymous_rejected(self):
        communicator = await _connect(AnonymousUser())
        assert (await communicator.receive_output(timeout=1))['code'] == CLOSE_UNAUTHENTICATED

    async def test_user_without_clinic_rejected(self, superuser):
        communicator = await _connect(superuser)
        assert (await communicator.receive_output(timeout=1))['code'] == CLOSE_NO_CLINIC

    async def test_receives_own_clinic_events_only(self, staff_user, clinic_b):
        communicator = await _connect(staff_user)
        layer = get_channel_layer()
        event = {'type': 'notification_new', 'payload': {'kind': 'chat_new'}}
        await layer.group_send(clinic_group_name(NOTIFICATIONS_STREAM, clinic_b.clinic_id), event)
        assert await communicator.receive_nothing(timeout=0.2)
        await layer.group_send(clinic_group_name(NOTIFICATIONS_STREAM, staff_user.clinic_id), event)
        assert (await communicator.receive_json_from(timeout=1))['kind'] == 'chat_new'
        await communicator.disconnect()
