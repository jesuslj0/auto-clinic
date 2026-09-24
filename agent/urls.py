from django.urls import path

from agent.views import (
    ChatInboxView,
    ChatMediaView,
    ChatMessagesFragmentView,
    ChatSessionListFragmentView,
    ChatSendMessageView,
    ChatToggleAgentView,
    ClinicAgentSwitchView,
)

app_name = 'agent'

urlpatterns = [
    path('', ChatInboxView.as_view(), name='chat-inbox'),
    path('agente/', ClinicAgentSwitchView.as_view(), name='agent-switch'),
    path('lista/', ChatSessionListFragmentView.as_view(), name='chat-list-fragment'),
    path('media/<uuid:message_id>/', ChatMediaView.as_view(), name='chat-media'),
    path('<uuid:session_id>/', ChatInboxView.as_view(), name='chat-thread'),
    path('<uuid:session_id>/mensajes/', ChatMessagesFragmentView.as_view(), name='chat-messages-fragment'),
    path('<uuid:session_id>/enviar/', ChatSendMessageView.as_view(), name='chat-send'),
    path('<uuid:session_id>/modo/', ChatToggleAgentView.as_view(), name='chat-toggle-agent'),
]
