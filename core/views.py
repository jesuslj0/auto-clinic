from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import viewsets

from appointments.models import Appointment
from core.models import Clinic, User
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin
from core.serializers import ClinicSerializer, UserSerializer


class ClinicViewSet(viewsets.ModelViewSet):
    queryset = Clinic.objects.all()
    serializer_class = ClinicSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['name', 'slug']


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['email', 'first_name', 'last_name']

    def get_queryset(self):
        user = self.request.user
        queryset = User.objects.select_related('clinic')
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class DashboardView(TemplateView):
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
