from django.urls import path

from agent.views import (
    ChatInboxView,
    ChatSendMessageView,
    ChatToggleAgentView,
    ClinicAgentSwitchView,
)

app_name = 'agent'

urlpatterns = [
    path('', ChatInboxView.as_view(), name='chat-inbox'),
    path('agente/', ClinicAgentSwitchView.as_view(), name='agent-switch'),
    path('<uuid:session_id>/', ChatInboxView.as_view(), name='chat-thread'),
    path('<uuid:session_id>/enviar/', ChatSendMessageView.as_view(), name='chat-send'),
    path('<uuid:session_id>/modo/', ChatToggleAgentView.as_view(), name='chat-toggle-agent'),
]
