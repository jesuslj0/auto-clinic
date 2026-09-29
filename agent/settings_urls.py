from django.urls import path

from agent.settings_views import (
    AgentConnectionSettingsView,
    AgentPersonaSettingsView,
    AgentTestMessageView,
    AgentTestView,
)

# Configuración del agente de WhatsApp (`/agente/`). Namespace propio porque
# `agent` ya es el de la bandeja de chats (`/chats/`).
app_name = 'agent_settings'

urlpatterns = [
    path('', AgentTestView.as_view(), name='test'),
    path('probar/enviar/', AgentTestMessageView.as_view(), name='test-send'),
    path('personalidad/', AgentPersonaSettingsView.as_view(), name='persona'),
    path('configuracion/', AgentConnectionSettingsView.as_view(), name='config'),
]
