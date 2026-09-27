import json

from core.consumers import ClinicScopedConsumer

CHATS_STREAM = 'chats'


class ChatConsumer(ClinicScopedConsumer):
    """Avisa en vivo de los cambios en los chats de la clínica del usuario.

    Solo manda avisos (qué hilo cambió), nunca el contenido: el navegador pide
    el fragmento HTML a Django al recibirlos. Ver `agent/realtime.py`.
    """

    stream = CHATS_STREAM

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    async def chat_session(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    async def chat_clinic(self, event):
        await self.send(text_data=json.dumps(event['payload']))
