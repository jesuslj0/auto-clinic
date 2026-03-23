from datetime import timedelta

from django.shortcuts import redirect
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from services.models import Service


class BookingServiceListView(TemplateView):
    template_name = 'booking/service_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        services = Service.objects.filter(is_active=True).order_by('name')
        for service in services:
            service.booking_url = reverse('booking:datetime') + f'?service={service.pk}'
        context['services'] = services
        return context


class BookingDateTimeView(TemplateView):
    template_name = 'booking/select_datetime.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        service = self._get_service()
        start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        context.update(
            {
                'service': service,
                'available_slots': [start + timedelta(days=day, hours=offset) for day in range(3) for offset in (0, 2, 4)],
                'booking_confirm_url': reverse('booking:confirm'),
            }
        )
        return context

    def _get_service(self):
        service_id = self.request.GET.get('service')
        if service_id:
            return Service.objects.filter(pk=service_id).first()
        return Service.objects.filter(is_active=True).order_by('name').first()


class BookingConfirmView(TemplateView):
    template_name = 'booking/confirm.html'

    def post(self, request, *args, **kwargs):
        return redirect('booking:success')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        service = self._get_service()
        selected_datetime_value = self.request.GET.get('datetime') or self.request.POST.get('selected_datetime')
        selected_datetime = parse_datetime(selected_datetime_value) if selected_datetime_value else None
        context.update(
            {
                'service': service,
                'selected_datetime': selected_datetime or timezone.now(),
            }
        )
        return context

    def _get_service(self):
        service_id = self.request.GET.get('service') or self.request.POST.get('service_id')
        if service_id:
            return Service.objects.filter(pk=service_id).first()
        return None


class BookingSuccessView(TemplateView):
    template_name = 'booking/success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'booking_services_url': reverse('booking:service_list'),
                'dashboard_url': reverse('core:dashboard'),
            }
        )
        return context
