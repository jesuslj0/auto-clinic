from django.urls import path

from agent.views import (
    ChatInboxView,
    ChatMediaView,
    ChatMessagesFragmentView,
    ChatSessionListFragmentView,
    ChatSendMessageView,
    ChatToggleAgentView,
    ChatTypingView,
    ClinicAgentSwitchView,
    N8nHealthView,
)

app_name = 'agent'

urlpatterns = [
    path('', ChatInboxView.as_view(), name='chat-inbox'),
    path('agente/', ClinicAgentSwitchView.as_view(), name='agent-switch'),
    path('n8n-estado/', N8nHealthView.as_view(), name='n8n-health'),
    path('lista/', ChatSessionListFragmentView.as_view(), name='chat-list-fragment'),
    path('adjuntos/<uuid:message_id>/', ChatMediaView.as_view(), name='chat-media'),
    path('<uuid:session_id>/', ChatInboxView.as_view(), name='chat-thread'),
    path('<uuid:session_id>/mensajes/', ChatMessagesFragmentView.as_view(), name='chat-messages-fragment'),
    path('<uuid:session_id>/enviar/', ChatSendMessageView.as_view(), name='chat-send'),
    path('<uuid:session_id>/escribiendo/', ChatTypingView.as_view(), name='chat-typing'),
    path('<uuid:session_id>/modo/', ChatToggleAgentView.as_view(), name='chat-toggle-agent'),
]
