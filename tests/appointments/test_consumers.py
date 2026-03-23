"""Tests for AppointmentConsumer WebSocket handler."""
import json

import pytest
from channels.testing import WebsocketCommunicator

from config.asgi import application


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestAppointmentConsumer:
    async def test_connect_and_disconnect(self):
        communicator = WebsocketCommunicator(
            application, "/ws/appointments/1/"
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    async def test_receives_appointment_update_message(self):
        from channels.layers import get_channel_layer

        communicator = WebsocketCommunicator(
            application, "/ws/appointments/42/"
        )
        connected, _ = await communicator.connect()
        assert connected is True

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "clinic_42_appointments",
            {
                "type": "appointment_update",
                "payload": {
                    "id": 1,
                    "status": "confirmed",
                    "scheduled_at": "2026-03-23T10:00:00",
                    "created": False,
                },
            },
        )

        response = await communicator.receive_json_from(timeout=3)
        assert response["status"] == "confirmed"
        assert response["id"] == 1
        await communicator.disconnect()

    async def test_different_clinic_group_isolation(self):
        from channels.layers import get_channel_layer

        comm_clinic_1 = WebsocketCommunicator(application, "/ws/appointments/1/")
        comm_clinic_2 = WebsocketCommunicator(application, "/ws/appointments/2/")

        await comm_clinic_1.connect()
        await comm_clinic_2.connect()

        channel_layer = get_channel_layer()
        # Send to clinic 1 group only
        await channel_layer.group_send(
            "clinic_1_appointments",
            {
                "type": "appointment_update",
                "payload": {"id": 99, "status": "pending", "scheduled_at": "2026-03-23T10:00:00", "created": True},
            },
        )

        # clinic 1 should receive it
        msg = await comm_clinic_1.receive_json_from(timeout=3)
        assert msg["id"] == 99

        # clinic 2 should NOT receive it
        assert await comm_clinic_2.receive_nothing(timeout=1) is True

        await comm_clinic_1.disconnect()
        await comm_clinic_2.disconnect()
