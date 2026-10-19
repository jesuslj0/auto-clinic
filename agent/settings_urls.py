from django.urls import path

from agent.settings_views import (
    AgentConnectionSettingsView,
    AgentPersonaSettingsView,
    AgentTestMessageView,
    AgentTestView,
)
from agent.template_views import (
    TemplateClearPurposeView,
    TemplateDeleteView,
    TemplateDuplicateView,
    TemplateFormView,
    TemplateListView,
    TemplateReminderView,
    TemplateSubmitView,
    TemplateSyncView,
)

# Configuración del agente de WhatsApp (`/agente/`). Namespace propio porque
# `agent` ya es el de la bandeja de chats (`/chats/`).
app_name = 'agent_settings'

urlpatterns = [
    path('', AgentTestView.as_view(), name='test'),
    path('probar/enviar/', AgentTestMessageView.as_view(), name='test-send'),
    path('personalidad/', AgentPersonaSettingsView.as_view(), name='persona'),
    path('configuracion/', AgentConnectionSettingsView.as_view(), name='config'),
    path('plantillas/', TemplateListView.as_view(), name='templates'),
    path('plantillas/nueva/', TemplateFormView.as_view(), name='template-create'),
    path('plantillas/sincronizar/', TemplateSyncView.as_view(), name='template-sync'),
    path('plantillas/<int:pk>/', TemplateFormView.as_view(), name='template-edit'),
    path('plantillas/<int:pk>/enviar/', TemplateSubmitView.as_view(), name='template-submit'),
    path('plantillas/<int:pk>/recordatorio/', TemplateReminderView.as_view(), name='template-reminder'),
    path('plantillas/<int:pk>/quitar-uso/', TemplateClearPurposeView.as_view(), name='template-clear-purpose'),
    path('plantillas/<int:pk>/duplicar/', TemplateDuplicateView.as_view(), name='template-duplicate'),
    path('plantillas/<int:pk>/eliminar/', TemplateDeleteView.as_view(), name='template-delete'),
]
