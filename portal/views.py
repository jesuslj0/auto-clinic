from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from appointments.models import Appointment


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


class PortalAppointmentActionView(View):
    status_value = None
    response_message = ''

    def post(self, request, *args, **kwargs):
        appointment = get_object_or_404(Appointment, confirmation_token=kwargs['token'])
        appointment.status = self.status_value
        appointment.save(update_fields=['status', 'updated_at'])
        return HttpResponse(self.response_message)


class PortalAppointmentConfirmView(PortalAppointmentActionView):
    status_value = Appointment.Status.CONFIRMED
    response_message = 'Appointment confirmed.'


class PortalAppointmentCancelView(PortalAppointmentActionView):
    status_value = Appointment.Status.CANCELLED
    response_message = 'Appointment cancelled.'
