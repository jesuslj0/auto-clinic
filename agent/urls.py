from django.urls import path

from agent.views import ChatInboxView

app_name = 'agent'

urlpatterns = [
    path('', ChatInboxView.as_view(), name='chat-inbox'),
    path('<uuid:session_id>/', ChatInboxView.as_view(), name='chat-thread'),
]
