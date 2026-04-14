from datetime import datetime, time, timedelta

import django_filters
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import Appointment
from appointments.serializers import AppointmentSerializer
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.models import Clinic
from core.permissions import IsStaffOrAdmin


class AppointmentFilter(django_filters.FilterSet):
    clinic = django_filters.CharFilter(field_name='clinic_id')
    status = django_filters.BaseInFilter(field_name='status', lookup_expr='in')
    service = django_filters.NumberFilter(field_name='service_id')
    patient = django_filters.NumberFilter(field_name='patient_id')
    professional = django_filters.NumberFilter(field_name='professional_id')
    patient_phone = django_filters.CharFilter(field_name='patient__phone', lookup_expr='exact')
    reminder_24h_sent = django_filters.BooleanFilter(field_name='reminder_24h_sent')
    reminder_3h_sent = django_filters.BooleanFilter(field_name='reminder_3h_sent')
    reminder_responded = django_filters.BooleanFilter(field_name='reminder_responded')
    scheduled_at_after = django_filters.IsoDateTimeFilter(field_name='scheduled_at', lookup_expr='gte')
    scheduled_at_before = django_filters.IsoDateTimeFilter(field_name='scheduled_at', lookup_expr='lte')
    status_exclude = django_filters.CharFilter(method='filter_status_exclude')

    def filter_status_exclude(self, queryset, name, value):
        return queryset.exclude(status=value)

    class Meta:
        model = Appointment
        fields = [
            'clinic', 'status', 'service', 'patient', 'professional', 'patient_phone',
            'reminder_24h_sent', 'reminder_3h_sent', 'reminder_responded',
        ]


class AppointmentViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['patient__first_name', 'patient__last_name', 'patient_name', 'patient_phone', 'status']
    filterset_class = AppointmentFilter
    ordering_fields = ['scheduled_at', 'status', 'created_at', 'patient_name']
    ordering = ['scheduled_at']

    def get_queryset(self):
        queryset = Appointment.objects.select_related('clinic', 'patient', 'service', 'professional__user')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    @action(detail=False, methods=['get'], url_path='available-slots')
    def available_slots(self, request):
        date_str = request.query_params.get('date')
        clinic_param = request.query_params.get('clinic')

        if clinic_param:
            try:
                clinic = Clinic.objects.get(pk=clinic_param)
            except Clinic.DoesNotExist:
                return Response({'detail': 'Clínica no encontrada.'}, status=status.HTTP_400_BAD_REQUEST)
        elif request.user.clinic_id:
            clinic = request.user.clinic
        else:
            return Response({'detail': 'El usuario no tiene clínica asignada.'}, status=status.HTTP_400_BAD_REQUEST)

        if not date_str:
            return Response({'detail': 'El parámetro "date" es requerido (YYYY-MM-DD).'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'Formato de fecha inválido. Usa YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            duration = int(request.query_params.get('duration', 30))
            start_hour = int(request.query_params.get('start_hour', 8))
            end_hour = int(request.query_params.get('end_hour', 18))
        except ValueError:
            return Response({'detail': 'Los parámetros duration, start_hour y end_hour deben ser enteros.'}, status=status.HTTP_400_BAD_REQUEST)

        if duration <= 0 or start_hour < 0 or end_hour > 24 or start_hour >= end_hour:
            return Response({'detail': 'Parámetros de horario inválidos.'}, status=status.HTTP_400_BAD_REQUEST)

        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(datetime.combine(target_date, time(start_hour, 0)), tz)
        day_end = timezone.make_aware(datetime.combine(target_date, time(end_hour, 0)), tz)

        queryset = Appointment.objects.filter(
            clinic=clinic,
            scheduled_at__date=target_date,
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        ).select_related('service')

        busy = []
        for appt in queryset:
            appt_start = appt.scheduled_at
            if appt.end_at:
                appt_end = appt.end_at
            elif appt.service_id and appt.service:
                appt_end = appt_start + timedelta(minutes=appt.service.duration_minutes)
            else:
                appt_end = appt_start + timedelta(minutes=30)
            busy.append((appt_start, appt_end))

        slot_duration = timedelta(minutes=duration)
        slots = []
        current = day_start
        while current + slot_duration <= day_end:
            slot_end = current + slot_duration
            if not any(current < b_end and slot_end > b_start for b_start, b_end in busy):
                slots.append(current.isoformat())
            current += slot_duration

        return Response({'date': date_str, 'duration_minutes': duration, 'available_slots': slots})

    @action(detail=True, methods=['get'], url_path='status')
    def get_status(self, request, pk=None):
        appointment = self.get_object()
        return Response({
            'id': str(appointment.pk),
            'status': appointment.status,
            'patient_name': appointment.patient_name or str(appointment.patient),
            'scheduled_at': appointment.scheduled_at,
            'confirmation_token': str(appointment.confirmation_token),
        })
    
    @action(detail=False, methods=['get'], url_path='pending-reminders')
    def pending_reminders(self, request):
        reminder_type = request.query_params.get('type', '24h')
        now = timezone.now()
        
        if reminder_type == '24h':
            window_start = now + timedelta(hours=23)
            window_end = now + timedelta(hours=25)
            qs = self.get_queryset().filter(
                scheduled_at__range=(window_start, window_end),
                status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
                reminder_24h_sent=False,
            )
        else:  # 3h
            window_start = now + timedelta(hours=2, minutes=30)
            window_end = now + timedelta(hours=3, minutes=30)
            qs = self.get_queryset().filter(
                scheduled_at__range=(window_start, window_end),
                status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
                reminder_3h_sent=False,
            )
        
        serializer = self.get_serializer(qs, many=True)
        return Response({'results': serializer.data, 'count': qs.count()})



class AppointmentCalendarView(TemplateView):
    template_name = 'appointments/calendar.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        week_start = self._get_week_start()
        week_days = [week_start + timedelta(days=offset) for offset in range(7)]
        appointments = Appointment.objects.select_related('patient', 'service', 'professional__user').filter(
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
        appointments = Appointment.objects.select_related('patient', 'service', 'professional__user')
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
