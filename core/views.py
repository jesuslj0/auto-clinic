from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, UpdateView
from rest_framework import viewsets
from rest_framework.response import Response

from appointments.models import Appointment
from core.forms import ClinicForm, EmailAuthenticationForm
from core.mixins import ExportMixin
from core.models import Clinic, User
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin
from core.serializers import ClinicSerializer, UserSerializer


class ClinicLoginView(LoginView):
    form_class = EmailAuthenticationForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


class ClinicLogoutView(LogoutView):
    next_page = reverse_lazy('core:login')


class ClinicViewSet(ExportMixin, viewsets.ModelViewSet):
    queryset = Clinic.objects.all()
    serializer_class = ClinicSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['name', 'clinic_id']
    ordering_fields = ['name', 'clinic_id']
    ordering = ['name']


class UserViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['email', 'first_name', 'last_name', 'role']
    ordering = ['email']

    def get_queryset(self):
        user = self.request.user
        queryset = User.objects.select_related('clinic')
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ClinicInfoView(LoginRequiredMixin, TemplateView):
    template_name = 'clinics/clinic_info.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['clinic_obj'] = self.request.user.clinic
        return context


class ClinicEditView(LoginRequiredMixin, UpdateView):
    model = Clinic
    form_class = ClinicForm
    template_name = 'clinics/clinic_edit.html'
    success_url = reverse_lazy('core:clinic-info')

    def get_object(self, queryset=None):
        return self.request.user.clinic

    def form_valid(self, form):
        messages.success(self.request, 'Información de la clínica actualizada correctamente.')
        return super().form_valid(form)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        appointments = Appointment.objects.select_related('patient', 'service').order_by('scheduled_at')
        today_schedule = appointments.filter(scheduled_at__date=today)
        context.update(
            {
                'today': today,
                'today_schedule': today_schedule,
                'today_appointments': today_schedule.count(),
                'pending_appointments': appointments.filter(status=Appointment.Status.PENDING).count(),
                'cancelled_appointments': appointments.filter(status=Appointment.Status.CANCELLED).count(),
            }
        )
        return context
