import json

from core.consumers import ClinicScopedConsumer

APPOINTMENTS_STREAM = 'appointments'


class AppointmentConsumer(ClinicScopedConsumer):
    """Avisa en vivo de los cambios en las citas de la clínica del usuario."""

    stream = APPOINTMENTS_STREAM

    async def appointment_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))
