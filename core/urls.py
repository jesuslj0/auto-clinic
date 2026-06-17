from django.urls import path

from core.views import (
    AppointmentQuickDetailView,
    ClinicEditView,
    ClinicInfoView,
    ClinicLoginView,
    ClinicLogoutView,
    DashboardAppointmentActionView,
    DashboardAppointmentManageView,
    DashboardView,
    SearchView,
)

app_name = 'core'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('buscar/', SearchView.as_view(), name='search'),
    path('login/', ClinicLoginView.as_view(), name='login'),
    path('logout/', ClinicLogoutView.as_view(), name='logout'),
    path('clinic/info/', ClinicInfoView.as_view(), name='clinic-info'),
    path('clinic/edit/', ClinicEditView.as_view(), name='clinic-edit'),
    path('dashboard/appointments/<uuid:appointment_id>/gestionar/', DashboardAppointmentManageView.as_view(), name='dashboard-manage-appointment'),
    path('dashboard/appointments/<uuid:appointment_id>/action/', DashboardAppointmentActionView.as_view(), name='dashboard-appointment-action'),
    path('dashboard/appointments/<uuid:appointment_id>/quick/', AppointmentQuickDetailView.as_view(), name='appointment-quick-detail'),
]
