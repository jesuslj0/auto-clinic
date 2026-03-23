import json

from channels.generic.websocket import AsyncWebsocketConsumer


class AppointmentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.clinic_id = self.scope['url_route']['kwargs']['clinic_id']
        self.group_name = f'clinic_{self.clinic_id}_appointments'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def appointment_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))
