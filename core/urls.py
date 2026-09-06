from django.urls import path

from core.views import (
    AccountProfileView,
    AccountScheduleView,
    AccountView,
    AgentTestMessageView,
    AppointmentQuickDetailView,
    ClinicEditView,
    ClinicInfoView,
    ClinicLoginView,
    ClinicLogoutView,
    DashboardAppointmentActionView,
    DashboardAppointmentManageView,
    DashboardView,
    PasswordChangeSectionView,
    SearchView,
    WhatsAppIntegrationView,
)

app_name = 'core'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('buscar/', SearchView.as_view(), name='search'),
    path('login/', ClinicLoginView.as_view(), name='login'),
    path('logout/', ClinicLogoutView.as_view(), name='logout'),
    # «Mi cuenta». Cada pestaña tiene su URL real (no un querystring): se puede
    # compartir, marcar como favorita y recargar. HTMX las usa tal cual con
    # `hx-push-url`, y sin JavaScript siguen siendo enlaces normales. Las dos
    # últimas sólo existen para quien tiene ficha de profesional.
    path('cuenta/', AccountView.as_view(), name='account'),
    path('cuenta/perfil/', AccountProfileView.as_view(), name='account-profile'),
    path('cuenta/horario/', AccountScheduleView.as_view(), name='account-schedule'),
    path('cuenta/password/', PasswordChangeSectionView.as_view(), name='password-change'),
    path('clinic/info/', ClinicInfoView.as_view(), name='clinic-info'),
    path('clinic/edit/', ClinicEditView.as_view(), name='clinic-edit'),
    path('clinic/integraciones/', WhatsAppIntegrationView.as_view(), name='clinic-integrations'),
    path('clinic/integraciones/probar/', AgentTestMessageView.as_view(), name='clinic-agent-test'),
    path('dashboard/appointments/<uuid:appointment_id>/gestionar/', DashboardAppointmentManageView.as_view(), name='dashboard-manage-appointment'),
    path('dashboard/appointments/<uuid:appointment_id>/action/', DashboardAppointmentActionView.as_view(), name='dashboard-appointment-action'),
    path('dashboard/appointments/<uuid:appointment_id>/quick/', AppointmentQuickDetailView.as_view(), name='appointment-quick-detail'),
]
