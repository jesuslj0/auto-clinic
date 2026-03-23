from datetime import datetime, time, timedelta

from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import Appointment
from appointments.serializers import AppointmentSerializer
from core.permissions import IsStaffOrAdmin


class AppointmentViewSet(viewsets.ModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['patient__first_name', 'patient__last_name', 'service__name', 'status']
    filterset_fields = ['clinic', 'status', 'service', 'patient']

    def get_queryset(self):
        queryset = Appointment.objects.select_related('clinic', 'patient', 'service', 'assigned_to')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class AppointmentCalendarView(TemplateView):
    template_name = 'appointments/calendar.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        week_start = self._get_week_start()
        week_days = [week_start + timedelta(days=offset) for offset in range(7)]
        appointments = Appointment.objects.select_related('patient', 'service', 'assigned_to').filter(
            scheduled_at__date__gte=week_start,
            scheduled_at__date__lte=week_start + timedelta(days=6),
        )
        context.update(
            {
                'appointments': appointments,
                'week_start': week_start,
                'week_end': week_start + timedelta(days=6),
                'previous_week': week_start - timedelta(days=7),
                'next_week': week_start + timedelta(days=7),
                'week_days': week_days,
                'time_slots': [time(hour=hour) for hour in range(8, 18)],
            }
        )
        return context

    def _get_week_start(self):
        week_param = self.request.GET.get('week')
        if week_param:
            try:
                return datetime.strptime(week_param, '%Y-%m-%d').date()
            except ValueError:
                pass
        today = timezone.localdate()
        return today - timedelta(days=today.weekday())


class AppointmentListView(TemplateView):
    template_name = 'appointments/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        appointments = Appointment.objects.select_related('patient', 'service', 'assigned_to')
        selected_date = self.request.GET.get('date')
        selected_status = self.request.GET.get('status', '')
        if selected_date:
            appointments = appointments.filter(scheduled_at__date=selected_date)
        if selected_status:
            appointments = appointments.filter(status=selected_status)
        context.update(
            {
                'appointments': appointments,
                'selected_date': selected_date,
                'selected_status': selected_status,
                'status_choices': Appointment.Status.choices,
                'appointments_list_url': 'appointments:list',
            }
        )
        return context


class AppointmentActionByTokenAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, token, action):
        try:
            appointment = Appointment.objects.get(confirmation_token=token)
        except Appointment.DoesNotExist:
            return Response({'detail': 'Invalid token.'}, status=status.HTTP_404_NOT_FOUND)

        if action == 'confirm':
            appointment.status = Appointment.Status.CONFIRMED
        elif action == 'cancel':
            appointment.status = Appointment.Status.CANCELLED
        else:
            return Response({'detail': 'Unsupported action.'}, status=status.HTTP_400_BAD_REQUEST)

        appointment.save(update_fields=['status', 'updated_at'])
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_200_OK)
