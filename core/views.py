import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, UpdateView
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import Appointment, AppointmentStatusHistory
from patients.models import Patient
from core.forms import ClinicForm, EmailAuthenticationForm, WhatsAppIntegrationForm
from core.mixins import ExportMixin
from core.models import Clinic, User
from core.permissions import IsAgentMasterKey, IsClinicAdminOrReadOnly, IsStaffOrAdmin
from core.serializers import AgentConfigSerializer, ClinicSerializer, UserSerializer


class ClinicLoginView(LoginView):
    form_class = EmailAuthenticationForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


class ClinicLogoutView(LogoutView):
    next_page = reverse_lazy('core:login')


class ClinicViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ClinicSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['name', 'clinic_id']
    ordering_fields = ['name', 'clinic_id']
    ordering = ['name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Clinic.objects.all()
        if not user.clinic_id:
            return Clinic.objects.none()
        return Clinic.objects.filter(clinic_id=user.clinic_id)


class UserViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['email', 'first_name', 'last_name', 'role']
    ordering = ['email']

    def get_queryset(self):
        user = self.request.user
        queryset = User.objects.select_related('clinic', 'professional_profile')
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ClinicInfoView(LoginRequiredMixin, TemplateView):
    template_name = 'clinics/clinic_info.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['clinic_obj'] = self.request.user.clinic
        context['section'] = 'clinic'
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


class ClinicAdminRequiredMixin(LoginRequiredMixin):
    """Restringe el acceso a administradores de la clínica (o superusuarios)."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and not (user.is_superuser or getattr(user, 'role', None) == 'admin'):
            raise PermissionDenied('Solo los administradores pueden gestionar esta configuración.')
        return super().dispatch(request, *args, **kwargs)


class WhatsAppIntegrationView(ClinicAdminRequiredMixin, View):
    """Onboarding guiado para conectar el agente de WhatsApp de la clínica."""

    template_name = 'clinics/integration_settings.html'

    def get(self, request):
        clinic = request.user.clinic
        if clinic is None:
            messages.error(request, 'Tu usuario no está asociado a ninguna clínica.')
            return redirect('core:dashboard')
        return self._render(request, clinic, WhatsAppIntegrationForm(instance=clinic))

    def post(self, request):
        clinic = request.user.clinic
        if clinic is None:
            messages.error(request, 'Tu usuario no está asociado a ninguna clínica.')
            return redirect('core:dashboard')

        if request.POST.get('action') == 'rotate_api_key':
            clinic.agent_api_key = uuid.uuid4()
            clinic.save(update_fields=['agent_api_key'])
            messages.success(
                request,
                'Se ha generado una nueva clave del agente. Actualízala en n8n para no perder la conexión.',
            )
            return redirect('core:clinic-integrations')

        form = WhatsAppIntegrationForm(request.POST, instance=clinic)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuración del agente de WhatsApp guardada correctamente.')
            return redirect('core:clinic-integrations')
        return self._render(request, clinic, form)

    def _render(self, request, clinic, form):
        is_connected = bool(
            clinic.whatsapp_phone_number_id
            and clinic.whatsapp_token
            and clinic.whatsapp_verify_token
        )
        context = {
            'clinic_obj': clinic,
            'form': form,
            'section': 'integrations',
            'webhook_url': settings.WHATSAPP_WEBHOOK_URL,
            'has_token': bool(clinic.whatsapp_token),
            'is_connected': is_connected,
        }
        return render(request, self.template_name, context)


class AgentTestMessageView(ClinicAdminRequiredMixin, View):
    """Proxy hacia el webhook de prueba de n8n.

    Recibe un mensaje del panel, lo reenvía a n8n con las credenciales del
    lado servidor (nunca se exponen en el navegador) y devuelve la respuesta
    del agente como JSON.
    """

    def post(self, request):
        import json
        import urllib.error
        import urllib.request
        from django.http import JsonResponse

        clinic = request.user.clinic
        if clinic is None:
            return JsonResponse({'error': 'Tu usuario no está asociado a ninguna clínica.'}, status=400)

        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Petición no válida.'}, status=400)

        message = (body.get('message') or '').strip()
        if not message:
            return JsonResponse({'error': 'El mensaje no puede estar vacío.'}, status=400)

        # Modo eco temporal: responde sin llamar a n8n (validación de la UI).
        if settings.WHATSAPP_TEST_ECHO_MODE:
            return JsonResponse({
                'reply': f'🤖 (modo eco de prueba) Recibí: “{message}”. '
                         'El agente real responderá cuando conectes el webhook de n8n.',
            })

        payload = json.dumps({
            'clinic_id': clinic.clinic_id,
            'phone': 'panel-test',
            'message': message,
        }).encode('utf-8')

        req = urllib.request.Request(
            settings.WHATSAPP_TEST_WEBHOOK_URL,
            data=payload,
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Api-Key {clinic.agent_api_key}',
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            return JsonResponse(
                {'error': f'n8n respondió {exc.code}. Revisa que el webhook de prueba esté activo.'},
                status=502,
            )
        except urllib.error.URLError:
            return JsonResponse(
                {'error': 'No se pudo contactar con n8n. Comprueba la URL del webhook de prueba.'},
                status=502,
            )

        # n8n puede devolver texto plano o JSON con distintas claves.
        reply = raw
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                reply = data.get('reply') or data.get('text') or data.get('message') or data.get('output') or raw
        except (json.JSONDecodeError, TypeError):
            pass

        reply = (reply or '').strip() or 'El agente no devolvió ninguna respuesta.'
        return JsonResponse({'reply': reply})


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        appointments = Appointment.objects.select_related('patient', 'service').order_by('scheduled_at')
        user = self.request.user
        if not user.is_superuser and user.clinic_id:
            appointments = appointments.filter(clinic=user.clinic)

        today_schedule = appointments.filter(scheduled_at__date=today)
        today_count = today_schedule.count()
        today_completed = today_schedule.filter(status=Appointment.Status.COMPLETED).count()
        today_completed_pct = round(today_completed / today_count * 100) if today_count else 0
        professional = getattr(user, 'professional_profile', None)

        # Inicio del mes actual para métricas mensuales
        month_start = today.replace(day=1)

        # Nuevos pacientes registrados este mes (scopeados por clínica)
        patients_qs = Patient.objects.all()
        if not user.is_superuser and user.clinic_id:
            patients_qs = patients_qs.filter(clinic=user.clinic)
        new_patients_month = patients_qs.filter(created_at__date__gte=month_start).count()

        # Ingresos del mes según citas completadas (suma del precio del servicio)
        revenue_month = (
            appointments.filter(
                status=Appointment.Status.COMPLETED,
                scheduled_at__date__gte=month_start,
            ).aggregate(total=Sum('service__price'))['total']
            or 0
        )

        context.update(
            {
                'today': today,
                'today_schedule': today_schedule,
                'today_appointments': today_count,
                'today_completed': today_completed,
                'today_completed_pct': today_completed_pct,
                'pending_appointments': appointments.filter(status=Appointment.Status.PENDING).count(),
                'cancelled_appointments': appointments.filter(status=Appointment.Status.CANCELLED).count(),
                'new_patients_month': new_patients_month,
                'revenue_month': revenue_month,
                'professional': professional,
                'section': 'dashboard',
            }
        )
        return context


class SearchView(LoginRequiredMixin, TemplateView):
    """Búsqueda unificada de pacientes y citas."""
    template_name = 'search/results.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        user = self.request.user

        patients = Patient.objects.none()
        appointments = Appointment.objects.none()

        if query:
            patients = Patient.objects.all()
            appointments = Appointment.objects.select_related('patient', 'service', 'professional__user')

            if not user.is_superuser and user.clinic_id:
                patients = patients.filter(clinic=user.clinic)
                appointments = appointments.filter(clinic=user.clinic)

            patients = patients.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )[:50]

            appointments = appointments.filter(
                Q(patient__first_name__icontains=query)
                | Q(patient__last_name__icontains=query)
                | Q(patient_name__icontains=query)
                | Q(patient_phone__icontains=query)
                | Q(service__name__icontains=query)
                | Q(service_name__icontains=query)
            ).order_by('-scheduled_at')[:50]

        context.update(
            {
                'query': query,
                'patients': patients,
                'appointments': appointments,
                'section': 'search',
            }
        )
        return context


class DashboardAppointmentActionView(LoginRequiredMixin, View):
    def post(self, request, appointment_id):
        appointment = get_object_or_404(Appointment, pk=appointment_id)

        user = request.user
        if not user.is_superuser:
            if not user.clinic_id or appointment.clinic_id != user.clinic_id:
                messages.error(request, 'No tienes permiso para gestionar esta cita.')
                return redirect('core:dashboard')

        action = request.POST.get('action')
        actor_label = user.get_full_name() or user.email
        if action == 'confirm':
            if appointment.status == Appointment.Status.CANCELLED:
                messages.error(request, 'Esta cita fue cancelada y no puede confirmarse.')
                next_url = request.POST.get('next') or 'core:dashboard'
                return redirect(next_url)
            if appointment.professional:
                # Solo bloquea si ya hay otra cita CONFIRMADA solapada; varias
                # pendientes en el mismo tramo pueden coexistir hasta confirmar.
                conflict = Appointment.find_overlap(
                    appointment.professional,
                    appointment.scheduled_at,
                    appointment.get_end_datetime(),
                    exclude_pk=appointment.pk,
                    statuses=[Appointment.Status.CONFIRMED],
                )
                if conflict:
                    messages.error(
                        request,
                        f'No se puede confirmar: {appointment.professional} ya tiene la cita '
                        f'"{conflict}" a las {conflict.scheduled_at:%H:%M} en ese mismo tramo horario.',
                    )
                    next_url = request.POST.get('next') or 'core:dashboard'
                    return redirect(next_url)
            prev = appointment.status
            appointment.status = Appointment.Status.CONFIRMED
            appointment.save(update_fields=['status', 'updated_at'])
            AppointmentStatusHistory.objects.create(
                appointment=appointment,
                from_status=prev,
                to_status=Appointment.Status.CONFIRMED,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
            )
            success_message = 'Cita confirmada correctamente.'
        elif action == 'reject':
            prev = appointment.status
            appointment.status = Appointment.Status.CANCELLED
            appointment.cancelled_by = Appointment.CancelledBy.STAFF
            appointment.save(update_fields=['status', 'cancelled_by', 'updated_at'])
            AppointmentStatusHistory.objects.create(
                appointment=appointment,
                from_status=prev,
                to_status=Appointment.Status.CANCELLED,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
            )
            success_message = 'Cita rechazada correctamente.'
        elif action == 'complete':
            if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW):
                messages.error(request, 'Esta cita no puede marcarse como completada.')
                next_url = request.POST.get('next') or 'core:dashboard'
                return redirect(next_url)
            prev = appointment.status
            appointment.status = Appointment.Status.COMPLETED
            appointment.save(update_fields=['status', 'updated_at'])
            AppointmentStatusHistory.objects.create(
                appointment=appointment,
                from_status=prev,
                to_status=Appointment.Status.COMPLETED,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
            )
            success_message = 'Cita marcada como completada.'
        elif action == 'no_show':
            if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.COMPLETED):
                messages.error(request, 'Esta cita no puede marcarse como no presentada.')
                next_url = request.POST.get('next') or 'core:dashboard'
                return redirect(next_url)
            prev = appointment.status
            appointment.status = Appointment.Status.NO_SHOW
            appointment.save(update_fields=['status', 'updated_at'])
            AppointmentStatusHistory.objects.create(
                appointment=appointment,
                from_status=prev,
                to_status=Appointment.Status.NO_SHOW,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
            )
            success_message = 'Cita marcada como no presentada.'
        else:
            return HttpResponseBadRequest('Acción no soportada.')

        messages.success(request, success_message)
        return redirect('core:dashboard')

class DashboardAppointmentManageView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/appointment_manage.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['appointment'] = self._get_scoped_appointment()
        context['section'] = 'dashboard'
        return context

    def _get_scoped_appointment(self):
        appointment = get_object_or_404(
            Appointment.objects.select_related('patient', 'service', 'professional__user', 'clinic')
                               .prefetch_related('status_history'),
            pk=self.kwargs['appointment_id'],
        )

        user = self.request.user
        if not user.is_superuser and (not user.clinic_id or appointment.clinic_id != user.clinic_id):
            raise PermissionDenied('No tienes permiso para gestionar esta cita.')

        return appointment


class AppointmentQuickDetailView(LoginRequiredMixin, View):
    """Devuelve un fragmento HTML con el resumen de una cita para el panel lateral del calendario."""

    def get(self, request, appointment_id):
        from django.template.loader import render_to_string
        appointment = get_object_or_404(
            Appointment.objects.select_related('patient', 'service', 'professional__user'),
            pk=appointment_id,
        )
        user = request.user
        if not user.is_superuser and (not user.clinic_id or appointment.clinic_id != user.clinic_id):
            raise PermissionDenied
        from django.http import HttpResponse
        html = render_to_string(
            'dashboard/_appointment_quick_detail.html',
            {'appointment': appointment, 'request': request},
        )
        return HttpResponse(html)



class AgentConfigView(APIView):
    permission_classes = [IsAgentMasterKey]
    authentication_classes = []

    def get(self, request, clinic_id):
        clinic = get_object_or_404(Clinic, clinic_id=clinic_id)
        return Response(AgentConfigSerializer(clinic).data)


class AgentConfigByPhoneView(APIView):
    permission_classes = [IsAgentMasterKey]
    authentication_classes = []

    def get(self, request):
        phone_number_id = request.query_params.get('phone_number_id', '').strip()
        if not phone_number_id:
            return Response(
                {'detail': 'El parámetro phone_number_id es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        clinic = get_object_or_404(Clinic, whatsapp_phone_number_id=phone_number_id)
        return Response(AgentConfigSerializer(clinic).data)
