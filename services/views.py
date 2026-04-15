from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView
from rest_framework import viewsets

from core.mixins import ExportMixin
from core.permissions import IsStaffOrAdmin
from services.forms import ServiceForm
from services.models import Service
from services.serializers import ServiceSerializer


class ServiceViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ServiceSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['name', 'description']
    filterset_fields = ['clinic', 'is_active']
    ordering_fields = ['name', 'price', 'duration_minutes', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        queryset = Service.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ServiceListView(LoginRequiredMixin, ListView):
    model = Service
    template_name = 'services/service_list.html'
    context_object_name = 'services'
    ordering = ['name']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        return context

    def get_queryset(self):
        queryset = Service.objects.select_related('clinic').order_by(*self.ordering)
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ServiceCreateView(LoginRequiredMixin, CreateView):
    model = Service
    form_class = ServiceForm
    template_name = 'services/service_form.html'
    success_url = reverse_lazy('services:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        return context

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada. Contacta con el administrador.')
            return redirect('services:list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.clinic = self.request.user.clinic
        messages.success(self.request, 'Servicio creado correctamente.')
        return super().form_valid(form)


class ServiceUpdateView(LoginRequiredMixin, UpdateView):
    model = Service
    form_class = ServiceForm
    template_name = 'services/service_form.html'
    success_url = reverse_lazy('services:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        return context

    def get_queryset(self):
        queryset = Service.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    def form_valid(self, form):
        messages.success(self.request, 'Servicio actualizado correctamente.')
        return super().form_valid(form)
