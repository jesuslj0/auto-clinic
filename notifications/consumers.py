import json

from core.consumers import ClinicScopedConsumer

NOTIFICATIONS_STREAM = 'notifications'


class NotificationConsumer(ClinicScopedConsumer):
    """Avisa en vivo de que hay notificaciones nuevas en la clínica del usuario.

    Sin contenido ni destinatarios: el navegador pide su contador por HTTP, que
    filtra por `request.user`, así que nada se filtra entre usuarios.
    """

    stream = NOTIFICATIONS_STREAM

    async def notification_new(self, event):
        await self.send(text_data=json.dumps(event['payload']))
