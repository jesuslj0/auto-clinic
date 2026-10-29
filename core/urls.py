from django.urls import path
from django.views.generic import RedirectView

from core import two_factor_views
from core.views import (
    AccountProfileView,
    AccountSecurityView,
    AccountScheduleView,
    AccountView,
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
    path('cuenta/contrasena/', PasswordChangeSectionView.as_view(), name='password-change'),
    path('cuenta/seguridad/', AccountSecurityView.as_view(), name='account-security'),
    # Verificación en dos pasos. Las de `/login/verificacion/…` no tienen sesión
    # todavía: solo el «login pendiente» (ver `core/two_factor.py`).
    path('login/verificacion/', two_factor_views.TwoFactorVerifyView.as_view(), name='two-factor-verify'),
    path('login/verificacion/configurar/', two_factor_views.TwoFactorLoginSetupView.as_view(), name='two-factor-setup'),
    path('cuenta/seguridad/activar/', two_factor_views.TwoFactorAccountSetupView.as_view(), name='two-factor-account-setup'),
    path('cuenta/seguridad/codigos/', two_factor_views.RecoveryCodesRegenerateView.as_view(), name='two-factor-recovery-codes'),
    path('cuenta/seguridad/cambiar-dispositivo/', two_factor_views.TwoFactorChangeDeviceView.as_view(), name='two-factor-change-device'),
    path('cuenta/seguridad/desactivar/', two_factor_views.TwoFactorDisableView.as_view(), name='two-factor-disable'),
    path('cuenta/seguridad/restablecer/<int:user_id>/', two_factor_views.TwoFactorResetView.as_view(), name='two-factor-reset'),
    path('clinica/', ClinicInfoView.as_view(), name='clinic-info'),
    path('clinica/editar/', ClinicEditView.as_view(), name='clinic-edit'),
    # El agente de WhatsApp tiene sección propia (`/agente/`). La ruta vieja
    # redirige: puede estar en un marcador.
    path(
        'clinica/integraciones/',
        RedirectView.as_view(pattern_name='agent_settings:test', permanent=False),
        name='clinic-integrations',
    ),
    path('panel/citas/<uuid:appointment_id>/gestionar/', DashboardAppointmentManageView.as_view(), name='dashboard-manage-appointment'),
    path('panel/citas/<uuid:appointment_id>/accion/', DashboardAppointmentActionView.as_view(), name='dashboard-appointment-action'),
    path('panel/citas/<uuid:appointment_id>/resumen/', AppointmentQuickDetailView.as_view(), name='appointment-quick-detail'),
]
