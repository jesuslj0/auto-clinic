from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from agent.views import AgentMemoryViewSet, ConversationSessionViewSet, WorkflowErrorViewSet
from appointments.views import AppointmentActionByTokenAPIView, AppointmentViewSet
from billing.views import SubscriptionViewSet
from core.views import ClinicViewSet, UserViewSet
from knowledge.views import ClinicInfoCacheViewSet, ClinicInfoQueryViewSet, ClinicKnowledgeBaseViewSet
from notifications.views import ReminderViewSet
from patients.views import PatientViewSet
from services.views import ServiceViewSet

router = DefaultRouter()

# Core
router.register(r'clinics', ClinicViewSet, basename='clinic')
router.register(r'users', UserViewSet, basename='user')

# Clinical data
router.register(r'patients', PatientViewSet, basename='patient')
router.register(r'services', ServiceViewSet, basename='service')
router.register(r'appointments', AppointmentViewSet, basename='appointment')
router.register(r'reminders', ReminderViewSet, basename='reminder')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')

# Knowledge base
router.register(r'knowledge/entries', ClinicKnowledgeBaseViewSet, basename='knowledge-entry')
router.register(r'knowledge/queries', ClinicInfoQueryViewSet, basename='knowledge-query')
router.register(r'knowledge/cache', ClinicInfoCacheViewSet, basename='knowledge-cache')

# Agent
router.register(r'agent/memory', AgentMemoryViewSet, basename='agent-memory')
router.register(r'agent/errors', WorkflowErrorViewSet, basename='agent-error')
router.register(r'agent/sessions', ConversationSessionViewSet, basename='agent-session')

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # Template views
    path('', include('core.urls')),
    path('appointments/', include('appointments.urls')),
    path('patients/', include('patients.urls')),
    path('booking/', include('booking.urls')),
    path('services/', include('services.urls')),
    path('portal/', include('portal.urls')),
    path('knowledge/', include('knowledge.urls')),

    # REST API
    path('api/', include(router.urls)),

    # Token auth for n8n (POST with username + password → returns token)
    path('api/auth/token/', obtain_auth_token, name='api-token-auth'),

    # Public appointment actions (no auth required)
    path(
        'api/public/appointments/<uuid:token>/<str:action>/',
        AppointmentActionByTokenAPIView.as_view(),
        name='appointment-token-action',
    ),

    # OpenAPI schema & Swagger UI
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
