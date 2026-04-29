from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from appointments.models import Appointment, AppointmentStatusHistory


class PortalAppointmentDetailView(TemplateView):
    template_name = 'portal/detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        appointment = get_object_or_404(
            Appointment.objects.select_related('patient', 'service'),
            confirmation_token=self.kwargs['token'],
        )
        context.update(
            {
                'appointment': appointment,
                'portal_confirm_url': reverse('portal:confirm', kwargs={'token': appointment.confirmation_token}),
                'portal_cancel_url': reverse('portal:cancel', kwargs={'token': appointment.confirmation_token}),
            }
        )
        return context


class PortalAppointmentConfirmView(View):
    def post(self, request, *args, **kwargs):
        appointment = get_object_or_404(Appointment, confirmation_token=kwargs['token'])
        if appointment.status == Appointment.Status.CANCELLED:
            return HttpResponse(
                '<p class="text-rose-600 font-medium">Esta cita fue cancelada y no puede confirmarse.</p>'
            )
        prev = appointment.status
        appointment.status = Appointment.Status.CONFIRMED
        appointment.save(update_fields=['status', 'updated_at'])
        AppointmentStatusHistory.objects.create(
            appointment=appointment,
            from_status=prev,
            to_status=Appointment.Status.CONFIRMED,
            actor=AppointmentStatusHistory.Actor.PATIENT,
            actor_label='Paciente (portal)',
        )
        return HttpResponse(
            '<div id="portal-actions">'
            '<div class="rounded-2xl bg-emerald-50 px-4 py-4 ring-1 ring-emerald-200">'
            '<div class="flex items-center gap-3">'
            '<svg class="h-5 w-5 flex-shrink-0 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">'
            '<path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />'
            '</svg>'
            '<p class="text-sm font-semibold text-emerald-700">Cita confirmada correctamente. ¡Te esperamos!</p>'
            '</div></div></div>'
        )


class PortalAppointmentCancelView(View):
    def post(self, request, *args, **kwargs):
        appointment = get_object_or_404(Appointment, confirmation_token=kwargs['token'])
        prev = appointment.status
        appointment.status = Appointment.Status.CANCELLED
        appointment.cancelled_by = Appointment.CancelledBy.PATIENT
        appointment.save(update_fields=['status', 'cancelled_by', 'updated_at'])
        AppointmentStatusHistory.objects.create(
            appointment=appointment,
            from_status=prev,
            to_status=Appointment.Status.CANCELLED,
            actor=AppointmentStatusHistory.Actor.PATIENT,
            actor_label='Paciente (portal)',
        )
        return HttpResponse(
            '<div id="portal-actions">'
            '<div class="rounded-2xl bg-rose-50 px-4 py-4 ring-1 ring-rose-200">'
            '<div class="flex items-center gap-3">'
            '<svg class="h-5 w-5 flex-shrink-0 text-rose-500" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">'
            '<path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />'
            '</svg>'
            '<p class="text-sm font-semibold text-rose-700">Cita cancelada. Si cambias de opinión, contacta con la clínica.</p>'
            '</div></div></div>'
        )
