from datetime import datetime, time, timedelta

import django_filters
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, TemplateView, UpdateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.forms import ProfessionalForm
from appointments.models import Appointment, Professional, ProfessionalSchedule
from appointments.serializers import AppointmentSerializer, ProfessionalScheduleSerializer, ProfessionalSerializer
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



class ProfessionalFilter(django_filters.FilterSet):
    clinic = django_filters.CharFilter(field_name='clinic_id')
    professional_type = django_filters.CharFilter(field_name='professional_type')
    service = django_filters.NumberFilter(field_name='services', lookup_expr='exact')

    class Meta:
        model = Professional
        fields = ['clinic', 'professional_type', 'service']


class ProfessionalViewSet(viewsets.ModelViewSet):
    serializer_class = ProfessionalSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_class = ProfessionalFilter
    search_fields = ['user__first_name', 'user__last_name', 'user__email']
    ordering_fields = ['user__first_name', 'user__last_name', 'user__email', 'professional_type']
    ordering = ['user__first_name', 'user__last_name']

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    @action(detail=True, methods=['get'], url_path='available-slots')
    def available_slots(self, request, pk=None):
        professional = self.get_object()
        date_str = request.query_params.get('date')

        if not date_str:
            return Response(
                {'detail': 'El parámetro "date" es requerido (YYYY-MM-DD).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Formato de fecha inválido. Usa YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            duration = int(request.query_params.get('duration', 30))
        except ValueError:
            return Response(
                {'detail': 'El parámetro "duration" debe ser un entero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if duration <= 0:
            return Response({'detail': 'El parámetro "duration" debe ser mayor que 0.'}, status=status.HTTP_400_BAD_REQUEST)

        # Buscar horario del profesional para ese día de la semana (0=lunes … 6=domingo)
        day_of_week = target_date.weekday()
        try:
            schedule = ProfessionalSchedule.objects.get(
                professional=professional,
                day_of_week=day_of_week,
                is_active=True,
            )
        except ProfessionalSchedule.DoesNotExist:
            return Response({
                'professional_id': professional.pk,
                'professional_name': str(professional),
                'date': date_str,
                'works_this_day': False,
                'available_slots': [],
            })

        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(datetime.combine(target_date, schedule.start_time), tz)
        day_end = timezone.make_aware(datetime.combine(target_date, schedule.end_time), tz)

        busy_appointments = Appointment.objects.filter(
            professional=professional,
            scheduled_at__date=target_date,
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        ).select_related('service')

        busy = []
        for appt in busy_appointments:
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

        return Response({
            'professional_id': professional.pk,
            'professional_name': str(professional),
            'date': date_str,
            'works_this_day': True,
            'schedule': {
                'start_time': schedule.start_time.strftime('%H:%M'),
                'end_time': schedule.end_time.strftime('%H:%M'),
            },
            'duration_minutes': duration,
            'available_slots': slots,
        })

    @action(detail=True, methods=['get'], url_path='services')
    def services(self, request, pk=None):
        professional = self.get_object()
        from appointments.serializers import ServiceMinimalSerializer
        serializer = ServiceMinimalSerializer(professional.services.all(), many=True)
        return Response({'professional_id': professional.pk, 'services': serializer.data})


class ProfessionalScheduleFilter(django_filters.FilterSet):
    professional = django_filters.NumberFilter(field_name='professional_id')
    day_of_week = django_filters.NumberFilter(field_name='day_of_week')
    is_active = django_filters.BooleanFilter(field_name='is_active')

    class Meta:
        model = ProfessionalSchedule
        fields = ['professional', 'day_of_week', 'is_active']


class ProfessionalScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = ProfessionalScheduleSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_class = ProfessionalScheduleFilter
    ordering_fields = ['day_of_week', 'start_time']
    ordering = ['day_of_week']

    def get_queryset(self):
        queryset = ProfessionalSchedule.objects.select_related('professional__user', 'professional__clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(professional__clinic=user.clinic)


class AppointmentCalendarView(TemplateView):
    template_name = 'appointments/calendar.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        week_start = self._get_week_start()
        week_days = [week_start + timedelta(days=offset) for offset in range(7)]

        user = self.request.user

        # Determinar si el usuario tiene perfil de profesional
        try:
            professional = user.professional_profile if user.is_authenticated else None
        except Professional.DoesNotExist:
            professional = None

        appointments_qs = Appointment.objects.select_related('patient', 'service', 'professional__user').filter(
            scheduled_at__date__gte=week_start,
            scheduled_at__date__lte=week_start + timedelta(days=6),
        )
        schedules_qs = ProfessionalSchedule.objects.filter(is_active=True).select_related('professional__user')

        if professional:
            # Vista personal: solo sus citas y su horario
            appointments_qs = appointments_qs.filter(professional=professional)
            schedules_qs = schedules_qs.filter(professional=professional)
        elif user.is_authenticated and not user.is_superuser and user.clinic_id:
            # Admin de clínica: toda la clínica
            appointments_qs = appointments_qs.filter(clinic=user.clinic)
            schedules_qs = schedules_qs.filter(professional__clinic=user.clinic)

        appointments = appointments_qs

        # Agrupar por día de la semana
        schedule_by_dow = {}
        for s in schedules_qs:
            schedule_by_dow.setdefault(s.day_of_week, []).append(s)

        has_schedules = bool(schedule_by_dow)

        # Rango global de horas que cubre cualquier horario
        if has_schedules:
            global_start = min(s.start_time.hour for sl in schedule_by_dow.values() for s in sl)
            global_end = max(s.end_time.hour for sl in schedule_by_dow.values() for s in sl)
        else:
            global_start, global_end = 8, 18

        time_slots = [time(hour=h) for h in range(global_start, global_end)]

        # Construir info de cada columna del calendario
        week_days_info = []
        for day in week_days:
            dow = day.weekday()
            sched_list = schedule_by_dow.get(dow, [])

            if has_schedules and sched_list:
                working_hours = set()
                for s in sched_list:
                    working_hours.update(range(s.start_time.hour, s.end_time.hour))
                min_start = min(s.start_time for s in sched_list)
                max_end = max(s.end_time for s in sched_list)
                schedule_label = f'{min_start.strftime("%H:%M")}–{max_end.strftime("%H:%M")}'
                is_working = True
            elif has_schedules:
                working_hours = set()
                schedule_label = ''
                is_working = False
            else:
                # Sin horarios configurados: comportamiento anterior (todo activo)
                working_hours = set(range(global_start, global_end))
                schedule_label = ''
                is_working = True

            week_days_info.append({
                'date': day,
                'dow': dow,
                'is_working': is_working,
                'schedule_label': schedule_label,
                'working_hours': working_hours,
            })

        context.update({
            'appointments': appointments,
            'professional': professional,
            'week_start': week_start,
            'week_end': week_start + timedelta(days=6),
            'previous_week': week_start - timedelta(days=7),
            'next_week': week_start + timedelta(days=7),
            'week_days_info': week_days_info,
            'time_slots': time_slots,
            'has_schedules': has_schedules,
            'section': 'calendar',
        })
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
                'section': 'appointments',
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


class ProfessionalListView(LoginRequiredMixin, ListView):
    model = Professional
    template_name = 'appointments/professional_list.html'
    context_object_name = 'professionals'
    ordering = ['user__first_name', 'user__last_name', 'user__email']

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services').order_by(*self.ordering)
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'professionals'
        return context


class ProfessionalCreateView(LoginRequiredMixin, CreateView):
    model = Professional
    form_class = ProfessionalForm
    template_name = 'appointments/professional_form.html'
    success_url = reverse_lazy('appointments:professionals-list')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada.')
            return redirect('appointments:professionals-list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request_user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if self.request.user.is_superuser:
            form.instance.clinic = form.cleaned_data['user'].clinic
        else:
            form.instance.clinic = self.request.user.clinic
        messages.success(self.request, 'Profesional creado correctamente.')
        return super().form_valid(form)


class ProfessionalUpdateView(LoginRequiredMixin, UpdateView):
    model = Professional
    form_class = ProfessionalForm
    template_name = 'appointments/professional_form.html'
    success_url = reverse_lazy('appointments:professionals-list')

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request_user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if self.request.user.is_superuser:
            form.instance.clinic = form.cleaned_data['user'].clinic
        else:
            form.instance.clinic = self.request.user.clinic
        messages.success(self.request, 'Profesional actualizado correctamente.')
        return super().form_valid(form)
