from django.urls import path

from agent.consumers import ChatConsumer

websocket_urlpatterns = [
    path('ws/chats/', ChatConsumer.as_asgi()),
]
