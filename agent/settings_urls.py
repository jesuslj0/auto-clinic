from django.urls import path

from agent.settings_views import (
    AgentMetaSettingsView,
    AgentPersonaSettingsView,
    AgentTestMessageView,
    AgentTestView,
    AgentWebhookSettingsView,
)

# Configuración del agente de WhatsApp (`/agente/`). Namespace propio porque
# `agent` ya es el de la bandeja de chats (`/chats/`).
app_name = 'agent_settings'

urlpatterns = [
    path('', AgentTestView.as_view(), name='test'),
    path('probar/enviar/', AgentTestMessageView.as_view(), name='test-send'),
    path('personalidad/', AgentPersonaSettingsView.as_view(), name='persona'),
    path('meta/', AgentMetaSettingsView.as_view(), name='meta'),
    path('webhook/', AgentWebhookSettingsView.as_view(), name='webhook'),
]
