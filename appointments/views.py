from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import django_filters
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, FormView, ListView, TemplateView, UpdateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.forms import (
    AppointmentForm,
    ProfessionalForm,
    ProfessionalProfileForm,
    ScheduleFormSet,
    TimeOffFormSet,
)
from appointments.models import Appointment, AppointmentStatusHistory, Professional, ProfessionalSchedule
from appointments.services import (
    AppointmentDomainError,
    cancel_appointment,
    confirm_by_clinic,
    create_appointment,
    get_clinic_available_slots,
    get_professional_availability,
    register_patient_confirmation,
)
from appointments.serializers import AppointmentSerializer, ProfessionalScheduleSerializer, ProfessionalSerializer
from core.authentication import ClinicAgent
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.models import Clinic
from core.permissions import IsAgentClinicKey, IsStaffOrAdmin
from patients.models import Patient
from services.models import Service


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
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    search_fields = ['patient__first_name', 'patient__last_name', 'patient_name', 'patient_phone', 'status']
    filterset_class = AppointmentFilter
    ordering_fields = ['scheduled_at', 'status', 'created_at', 'patient_name']
    ordering = ['-scheduled_at']

    def get_queryset(self):
        queryset = Appointment.objects.select_related('clinic', 'patient', 'service', 'professional__user')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    @action(detail=False, methods=['get'], url_path='available-slots')
    def available_slots(self, request):
        date_str = request.query_params.get('date')
        clinic_param = request.query_params.get('clinic')
        user = request.user

        # Bug 5: control de acceso — solo superusuario puede consultar cualquier clínica
        if clinic_param:
            if not user.is_superuser and not isinstance(user, ClinicAgent):
                user_clinic_id = str(user.clinic_id) if user.clinic_id else None
                if user_clinic_id != str(clinic_param):
                    return Response({'detail': 'No tienes permiso para consultar esta clínica.'}, status=status.HTTP_403_FORBIDDEN)
            try:
                clinic = Clinic.objects.get(pk=clinic_param)
            except Clinic.DoesNotExist:
                return Response({'detail': 'Clínica no encontrada.'}, status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(user, ClinicAgent):
            clinic = user.clinic
        elif user.clinic_id:
            clinic = user.clinic
        else:
            return Response({'detail': 'El usuario no tiene clínica asignada.'}, status=status.HTTP_400_BAD_REQUEST)

        if not date_str:
            return Response({'detail': 'El parámetro "date" es requerido (YYYY-MM-DD).'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'Formato de fecha inválido. Usa YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Bug 6: no permitir fechas pasadas
        if target_date < timezone.localdate():
            return Response({'detail': 'No se pueden consultar slots para fechas pasadas.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            duration = int(request.query_params.get('duration', 30))
            start_hour = int(request.query_params.get('start_hour', 8))
            end_hour = int(request.query_params.get('end_hour', 18))
        except ValueError:
            return Response({'detail': 'Los parámetros duration, start_hour y end_hour deben ser enteros.'}, status=status.HTTP_400_BAD_REQUEST)

        if duration <= 0 or start_hour < 0 or end_hour > 24 or start_hour >= end_hour:
            return Response({'detail': 'Parámetros de horario inválidos.'}, status=status.HTTP_400_BAD_REQUEST)

        slots = get_clinic_available_slots(
            clinic,
            target_date,
            duration_minutes=duration,
            start_hour=start_hour,
            end_hour=end_hour,
        )

        return Response({
            'date': date_str,
            'duration_minutes': duration,
            'available_slots': [slot.isoformat() for slot in slots],
        })

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
    
    @action(
        detail=True,
        methods=['post'],
        url_path='confirm',
        permission_classes=[IsStaffOrAdmin],
    )
    def confirm(self, request, pk=None):
        """La clínica pone la cita en firme: `pending` → `confirmed`, y sin hold.

        Es la contrapartida autenticada de `/api/public/appointments/<token>/confirm/`,
        que es del paciente y NO cambia el estado. Confirmar es de la clínica, así
        que aquí no vale la API key del agente: solo staff o admin.
        """
        appointment = self.get_object()
        confirm_by_clinic(appointment, user=request.user)
        return Response(self.get_serializer(appointment).data)

    @action(detail=False, methods=['get'], url_path='pending-reminders')
    def pending_reminders(self, request):
        """Citas a las que toca mandarles recordatorio.

        Solo se recuerdan las citas que la clínica tiene EN FIRME: una `pending`
        es una cita a la espera de que el staff la valide, y no tiene sentido
        pedirle al paciente que confirme algo que la clínica aún no ha aceptado.

        `patient_confirmed_at` es lo que dice si el paciente ya respondió. Los
        flags `reminder_*` solo dicen qué se ha ENVIADO, que es lo suyo: ya no
        hacen de estado.
        """
        reminder_type = request.query_params.get('type', '24h')
        now = timezone.now()

        if reminder_type == '24h':
            window = (now + timedelta(hours=23), now + timedelta(hours=25))
            pendiente_de_envio = {'reminder_24h_sent': False}
        else:  # 3h
            window = (now + timedelta(hours=2, minutes=30), now + timedelta(hours=3, minutes=30))
            pendiente_de_envio = {'reminder_24h_sent': True, 'reminder_3h_sent': False}

        qs = self.get_queryset().filter(
            scheduled_at__range=window,
            status=Appointment.Status.CONFIRMED,
            patient_confirmed_at__isnull=True,
            **pendiente_de_envio,
        )

        serializer = self.get_serializer(qs, many=True)
        return Response({'results': serializer.data, 'count': qs.count()})



class ProfessionalFilter(django_filters.FilterSet):
    clinic = django_filters.CharFilter(field_name='clinic_id')
    professional_type = django_filters.CharFilter(field_name='professional_type')
    service = django_filters.NumberFilter(field_name='services', lookup_expr='exact')
    is_active = django_filters.BooleanFilter(field_name='is_active')
    accepts_online_booking = django_filters.BooleanFilter(field_name='accepts_online_booking')

    class Meta:
        model = Professional
        fields = ['clinic', 'professional_type', 'service', 'is_active', 'accepts_online_booking']


class ProfessionalViewSet(viewsets.ModelViewSet):
    serializer_class = ProfessionalSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    filterset_class = ProfessionalFilter
    search_fields = ['user__first_name', 'user__last_name', 'user__email']
    ordering_fields = ['user__first_name', 'user__last_name', 'user__email', 'professional_type']
    ordering = ['user__first_name', 'user__last_name']

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if not user.clinic_id:
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

        try:
            start_hour = request.query_params.get('start_hour')
            end_hour = request.query_params.get('end_hour')
            start_hour = int(start_hour) if start_hour is not None else None
            end_hour = int(end_hour) if end_hour is not None else None
        except ValueError:
            return Response(
                {'detail': 'Los parámetros start_hour y end_hour deben ser enteros.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        availability = get_professional_availability(
            professional,
            target_date,
            duration_minutes=duration,
            start_hour=start_hour,
            end_hour=end_hour,
        )

        if not availability.works_this_day:
            return Response({
                'professional_id': professional.pk,
                'professional_name': str(professional),
                'date': date_str,
                'works_this_day': False,
                'available_slots': [],
            })

        return Response({
            'professional_id': professional.pk,
            'professional_name': str(professional),
            'date': date_str,
            'works_this_day': True,
            'schedule': {
                'start_time': availability.schedule_start.strftime('%H:%M'),
                'end_time': availability.schedule_end.strftime('%H:%M'),
            },
            'duration_minutes': duration,
            'available_slots': [slot.isoformat() for slot in availability.slots],
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
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    filterset_class = ProfessionalScheduleFilter
    ordering_fields = ['day_of_week', 'start_time']
    ordering = ['day_of_week']

    def get_queryset(self):
        queryset = ProfessionalSchedule.objects.select_related('professional__user', 'professional__clinic')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(professional__clinic=user.clinic)
        if not user.clinic_id:
            return queryset
        return queryset.filter(professional__clinic=user.clinic)


class AppointmentCalendarView(LoginRequiredMixin, TemplateView):
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
        ).exclude(status=Appointment.Status.CANCELLED)
        schedules_qs = ProfessionalSchedule.objects.filter(is_active=True).select_related('professional__user')

        if professional:
            # Vista personal: solo sus citas y su horario
            appointments_qs = appointments_qs.filter(professional=professional)
            schedules_qs = schedules_qs.filter(professional=professional)
        elif user.clinic_id:
            # Admin de clínica (o superusuario con clínica): toda su clínica
            appointments_qs = appointments_qs.filter(clinic=user.clinic)
            schedules_qs = schedules_qs.filter(professional__clinic=user.clinic)
        elif user.is_superuser:
            # Superusuario de plataforma (sin clínica): ve todas las clínicas
            pass
        else:
            # Usuario autenticado sin clínica ni perfil: no ve nada de otras clínicas
            appointments_qs = appointments_qs.none()
            schedules_qs = schedules_qs.none()

        # Materializar para poder anotar cada cita con su columna de solapamiento.
        appointments = list(appointments_qs)
        self._assign_overlap_columns(appointments)

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
                # Parón entre turnos (jornada partida): horas que caen DENTRO de la
                # jornada pero no las cubre ningún tramo. Ej. 14–15 si trabaja
                # 9–14 y 16–20. Se distingue así de las horas de fuera de jornada.
                break_hours = set(range(min_start.hour, max_end.hour)) - working_hours
                schedule_label = f'{min_start.strftime("%H:%M")}–{max_end.strftime("%H:%M")}'
                is_working = True
            elif has_schedules:
                working_hours = set()
                break_hours = set()
                schedule_label = ''
                is_working = False
            else:
                # Sin horarios configurados: comportamiento anterior (todo activo)
                working_hours = set(range(global_start, global_end))
                break_hours = set()
                schedule_label = ''
                is_working = True

            week_days_info.append({
                'date': day,
                'dow': dow,
                'is_working': is_working,
                'schedule_label': schedule_label,
                'working_hours': working_hours,
                'break_hours': break_hours,
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

    @staticmethod
    def _assign_overlap_columns(appointments):
        """Reparte en columnas las citas que se solapan en el tiempo (mismo día).

        Sin esto, dos citas en el mismo tramo (dos profesionales a las 10:00, o
        dos pendientes) se pintan una encima de otra y solo se ve la de arriba.
        A cada cita le fija `col_left` y `col_width` (porcentajes del ancho de la
        casilla) para que N solapadas ocupen 1/N cada una, lado a lado.
        """
        by_day = {}
        for appt in appointments:
            dia = timezone.localtime(appt.scheduled_at).date()
            by_day.setdefault(dia, []).append(appt)

        for day_appts in by_day.values():
            day_appts.sort(key=lambda a: a.scheduled_at)
            # Un "cluster" es un grupo de citas encadenadas por solapamiento: se
            # reparten el ancho entre ellas, independientes de los demás clusters.
            cluster = []
            cluster_end = None
            for appt in day_appts:
                if cluster and appt.scheduled_at >= cluster_end:
                    AppointmentCalendarView._layout_cluster(cluster)
                    cluster = []
                    cluster_end = None
                cluster.append(appt)
                fin = appt.get_end_datetime()
                cluster_end = fin if cluster_end is None else max(cluster_end, fin)
            if cluster:
                AppointmentCalendarView._layout_cluster(cluster)

    @staticmethod
    def _layout_cluster(cluster):
        """Asigna columna a cada cita del cluster (greedy: primera columna libre)."""
        columns = []  # fin de la última cita colocada en cada columna
        for appt in cluster:
            for i, col_end in enumerate(columns):
                if appt.scheduled_at >= col_end:
                    columns[i] = appt.get_end_datetime()
                    appt.col_index = i
                    break
            else:
                appt.col_index = len(columns)
                columns.append(appt.get_end_datetime())

        # Como str (con punto): si se pasaran como float, la localización es_ES los
        # formatea con coma decimal ("50,0") y rompe el calc() del CSS.
        n = len(columns)
        for appt in cluster:
            appt.col_width = str(round(100 / n, 2))
            appt.col_left = str(round(appt.col_index * 100 / n, 2))

    def _get_week_start(self):
        week_param = self.request.GET.get('week')
        if week_param:
            try:
                return datetime.strptime(week_param, '%Y-%m-%d').date()
            except ValueError:
                pass
        today = timezone.localdate()
        return today - timedelta(days=today.weekday())


class AppointmentListView(LoginRequiredMixin, TemplateView):
    template_name = 'appointments/list.html'
    paginate_by = 20

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator
        context = super().get_context_data(**kwargs)
        appointments = Appointment.objects.select_related('patient', 'service', 'professional__user')

        # Aislamiento multitenant: cada usuario solo ve las citas de su clínica.
        # Un superusuario con clínica asignada queda acotado a ella; solo el
        # superusuario SIN clínica (equipo de plataforma) ve todas.
        user = self.request.user
        if user.clinic_id:
            appointments = appointments.filter(clinic=user.clinic)
        elif not user.is_superuser:
            appointments = appointments.none()

        # Por defecto (sin `date` en la query string) mostramos las citas de
        # hoy. Si el usuario limpia el campo de fecha a propósito, `date`
        # llega vacío en el GET y respetamos ese "todas las fechas".
        if 'date' in self.request.GET:
            selected_date = self.request.GET.get('date', '')
        else:
            selected_date = timezone.localdate().isoformat()

        selected_status = self.request.GET.get('status', '')
        sort_dir = self.request.GET.get('sort', 'asc')
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'asc'

        if selected_date:
            appointments = appointments.filter(scheduled_at__date=selected_date)
        if selected_status:
            appointments = appointments.filter(status=selected_status)

        appointments = appointments.order_by('scheduled_at' if sort_dir == 'asc' else '-scheduled_at')

        paginator = Paginator(appointments, self.paginate_by)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context.update(
            {
                'appointments': page_obj,
                'page_obj': page_obj,
                'paginator': paginator,
                'is_paginated': paginator.num_pages > 1,
                'selected_date': selected_date,
                'selected_status': selected_status,
                'sort_dir': sort_dir,
                'status_choices': Appointment.Status.choices,
                'appointments_list_url': 'appointments:list',
                'section': 'appointments',
            }
        )
        return context


class AppointmentCreateView(LoginRequiredMixin, FormView):
    """Alta manual de citas desde el panel (staff/admin).

    Acepta valores iniciales por query string (scheduled_at o date+time,
    patient, service) para que el calendario pueda enlazar aquí con la
    fecha/hora prerrellenadas.
    """

    template_name = 'appointments/appointment_form.html'
    form_class = AppointmentForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada.')
            return redirect('appointments:calendar')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['clinic'] = self.request.user.clinic
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        params = self.request.GET

        # scheduled_at ISO tiene prioridad; si no, date + time sueltos.
        scheduled_at = params.get('scheduled_at')
        if scheduled_at:
            parsed = timezone.datetime.fromisoformat(scheduled_at)
            if timezone.is_aware(parsed):
                tz = ZoneInfo(self.request.user.clinic.timezone)
                parsed = parsed.astimezone(tz)
            initial['date'] = parsed.date()
            initial['time'] = parsed.time().replace(second=0, microsecond=0)
        else:
            if params.get('date'):
                initial['date'] = params['date']
            if params.get('time'):
                initial['time'] = params['time']

        if params.get('patient'):
            initial['patient'] = params['patient']
        if params.get('service'):
            initial['service'] = params['service']

        # Profesional por defecto: el propio usuario si tiene perfil profesional
        # (salvo que venga otro indicado por query string).
        if params.get('professional'):
            initial['professional'] = params['professional']
        else:
            try:
                initial['professional'] = self.request.user.professional_profile.pk
            except Professional.DoesNotExist:
                pass
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'appointments'
        # Enlace de alta rápida de paciente que vuelve a este mismo form.
        new_patient_next = self.request.get_full_path()
        context['new_patient_url'] = f"{reverse_lazy('patients:create')}?{urlencode({'next': new_patient_next})}"

        # URLs de búsqueda para los combobox dinámicos de paciente/servicio.
        context['patients_search_url'] = reverse('patient-list')
        context['services_search_url'] = reverse('service-list')

        # Texto a mostrar en el combobox al cargar: si venimos de un POST
        # inválido usamos lo que el usuario había tecleado; si no, resolvemos
        # el paciente/servicio inicial (p. ej. prerrellenado desde el calendario).
        if self.request.method == 'POST':
            context['initial_patient_label'] = self.request.POST.get('patient_search', '')
            context['initial_service_label'] = self.request.POST.get('service_search', '')
        else:
            context['initial_patient_label'] = self._initial_label(
                Patient.objects.filter(clinic=self.request.user.clinic), self.request.GET.get('patient')
            )
            context['initial_service_label'] = self._initial_label(
                Service.objects.filter(clinic=self.request.user.clinic), self.request.GET.get('service')
            )
        return context

    @staticmethod
    def _initial_label(queryset, pk):
        if not pk:
            return ''
        obj = queryset.filter(pk=pk).first()
        return str(obj) if obj else ''

    def form_valid(self, form):
        data = form.cleaned_data
        actor_label = self.request.user.get_full_name() or self.request.user.email
        try:
            self.appointment = create_appointment(
                clinic=self.request.user.clinic,
                patient=data['patient'],
                service=data['service'],
                professional=data.get('professional'),
                scheduled_at=data['scheduled_at'],
                notes=data.get('notes', ''),
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
                # Alta desde el panel: el staff sí puede asignar citas a un
                # profesional que no acepta reservas online. Todo lo demás
                # (horario, ausencias, solapamientos) se sigue validando igual.
                require_online_booking=False,
                # La pone la clínica, así que nace en firme y sin hold: no hay
                # nada que validar después.
                source=Appointment.Source.STAFF,
            )
        except AppointmentDomainError as error:
            # El profesional elegido no puede atender la cita (horario, ausencia,
            # solapamiento…). El motivo viene del service; se muestra en el form.
            form.add_error('professional', error.detail['message'])
            return self.form_invalid(form)

        messages.success(self.request, 'Cita creada correctamente.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('appointments:calendar')


class AppointmentActionByTokenAPIView(APIView):
    """Respuesta del PACIENTE al recordatorio, sin autenticación (n8n la llama).

    Mismo contrato de siempre: 200 con la cita serializada, 404 si el token no
    existe, 400 si la acción no se reconoce. Lo que cambió es la semántica de
    `confirm`, no la forma de la respuesta:

      - "SÍ" → se registra `patient_confirmed_at`. El `status` NO se toca: el
        hueco ya estaba bloqueado y sigue estándolo. Confirmar la cita (ponerla
        en firme) es cosa de la clínica, no del paciente.
      - "NO" → se cancela, porque eso sí libera el hueco.
    """

    permission_classes = [AllowAny]

    def post(self, request, token, action):
        try:
            appointment = Appointment.objects.get(confirmation_token=token)
        except Appointment.DoesNotExist:
            return Response({'detail': 'Invalid token.'}, status=status.HTTP_404_NOT_FOUND)

        if action == 'confirm':
            register_patient_confirmation(appointment)
        elif action == 'cancel':
            cancel_appointment(
                appointment,
                actor=AppointmentStatusHistory.Actor.PATIENT,
                actor_label='Paciente (recordatorio)',
                cancelled_by=Appointment.CancelledBy.PATIENT,
            )
        else:
            return Response({'detail': 'Unsupported action.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_200_OK)


class ProfessionalListView(LoginRequiredMixin, ListView):
    model = Professional
    template_name = 'appointments/professional_list.html'
    context_object_name = 'professionals'
    ordering = ['user__first_name', 'user__last_name', 'user__email']

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services').order_by(*self.ordering)
        user = self.request.user
        if not user.clinic_id:
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
        if self.request.user.clinic_id:
            form.instance.clinic = self.request.user.clinic
        else:
            form.instance.clinic = form.cleaned_data['user'].clinic
        messages.success(self.request, 'Profesional creado correctamente.')
        return super().form_valid(form)


class ProfessionalProfileView(LoginRequiredMixin, UpdateView):
    """Perfil del propio profesional autenticado (foto y datos)."""

    form_class = ProfessionalProfileForm
    template_name = 'appointments/profile.html'
    success_url = reverse_lazy('appointments:profile')
    context_object_name = 'professional'

    def dispatch(self, request, *args, **kwargs):
        self.profile = None
        if request.user.is_authenticated:
            try:
                self.profile = request.user.professional_profile
            except Professional.DoesNotExist:
                messages.info(request, 'Tu usuario no tiene un perfil de profesional asociado.')
                return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.profile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'profile'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Perfil actualizado correctamente.')
        return super().form_valid(form)


class ProfessionalUpdateView(LoginRequiredMixin, UpdateView):
    model = Professional
    form_class = ProfessionalForm
    template_name = 'appointments/professional_form.html'
    success_url = reverse_lazy('appointments:professionals-list')

    def get_queryset(self):
        queryset = Professional.objects.select_related('user', 'clinic').prefetch_related('services')
        user = self.request.user
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request_user'] = self.request.user
        return kwargs

    def _clinic_tz(self):
        return ZoneInfo(self.object.clinic.timezone) if self.object.clinic_id else None

    def _build_formsets(self, data=None):
        """Los dos inline formsets del edit, atados al profesional que se edita.

        El de ausencias necesita la timezone de la clínica para convertir los
        instantes que teclea el staff (hora local) a los UTC que guarda la BD.
        """
        return {
            'schedule_formset': ScheduleFormSet(
                data, instance=self.object, prefix='schedules',
            ),
            'timeoff_formset': TimeOffFormSet(
                data, instance=self.object, prefix='timeoff',
                form_kwargs={'clinic_tz': self._clinic_tz()},
            ),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'professionals'
        # Al repintar tras un POST inválido, no re-vincular los formsets: ya vienen
        # en kwargs con sus errores. Solo se construyen frescos en el GET.
        if 'schedule_formset' not in context:
            context.update(self._build_formsets())
        return context

    def form_valid(self, form):
        if self.request.user.clinic_id:
            form.instance.clinic = self.request.user.clinic
        else:
            form.instance.clinic = form.cleaned_data['user'].clinic

        formsets = self._build_formsets(self.request.POST)
        if not all(fs.is_valid() for fs in formsets.values()):
            # Un formset inválido invalida todo el guardado: nada se escribe.
            return self.render_to_response(self.get_context_data(form=form, **formsets))

        self.object = form.save()
        for fs in formsets.values():
            fs.instance = self.object
            fs.save()

        messages.success(self.request, 'Profesional actualizado correctamente.')
        return redirect(self.get_success_url())
