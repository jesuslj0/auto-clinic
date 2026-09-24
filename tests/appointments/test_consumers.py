"""Tests for AppointmentConsumer WebSocket handler."""
import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from appointments.consumers import APPOINTMENTS_STREAM, AppointmentConsumer
from config.asgi import application
from core.consumers import CLOSE_NO_CLINIC, CLOSE_UNAUTHENTICATED
from core.realtime import clinic_group_name


def _update(appointment_id):
    return {
        "type": "appointment_update",
        "payload": {"id": appointment_id, "status": "confirmed", "scheduled_at": "2026-03-23T10:00:00", "created": False},
    }


async def _connect(user):
    communicator = WebsocketCommunicator(AppointmentConsumer.as_asgi(), "/ws/appointments/")
    communicator.scope["user"] = user
    connected, _ = await communicator.connect()
    return communicator, connected


async def _close_code(communicator):
    output = await communicator.receive_output(timeout=1)
    assert output["type"] == "websocket.close"
    return output["code"]


@pytest.mark.django_db(transaction=True)
class TestAppointmentConsumer:
    async def test_anonymous_is_rejected(self):
        # Antes aceptaba a cualquiera que supiera un clinic_id.
        communicator, _ = await _connect(AnonymousUser())
        assert await _close_code(communicator) == CLOSE_UNAUTHENTICATED

    async def test_route_without_session_is_rejected(self):
        communicator = WebsocketCommunicator(application, "/ws/appointments/")
        await communicator.connect()
        assert await _close_code(communicator) == CLOSE_UNAUTHENTICATED

    async def test_user_without_clinic_is_rejected(self, superuser):
        communicator, _ = await _connect(superuser)
        assert await _close_code(communicator) == CLOSE_NO_CLINIC

    async def test_receives_appointment_update_of_own_clinic(self, staff_user):
        communicator, connected = await _connect(staff_user)
        assert connected is True

        await get_channel_layer().group_send(
            clinic_group_name(APPOINTMENTS_STREAM, staff_user.clinic_id), _update(1)
        )

        response = await communicator.receive_json_from(timeout=3)
        assert response["status"] == "confirmed"
        assert response["id"] == 1
        await communicator.disconnect()

    async def test_different_clinic_group_isolation(self, staff_user, admin_user_b):
        comm_a, _ = await _connect(staff_user)
        comm_b, _ = await _connect(admin_user_b)

        await get_channel_layer().group_send(
            clinic_group_name(APPOINTMENTS_STREAM, staff_user.clinic_id), _update(99)
        )

        assert (await comm_a.receive_json_from(timeout=3))["id"] == 99
        assert await comm_b.receive_nothing(timeout=1) is True

        await comm_a.disconnect()
        await comm_b.disconnect()
