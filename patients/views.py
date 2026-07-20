from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch, Q
from django.shortcuts import redirect
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import viewsets
from django.urls import reverse_lazy

from appointments.models import Appointment
from core.authentication import ClinicAgent
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsAgentClinicKey, IsStaffOrAdmin
from patients.filters import PatientFilter
from patients.models import Patient
from patients.serializers import PatientSerializer
from patients.forms import PatientForm
from patients.services import create_patient


class PatientViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    search_fields = ["first_name", "last_name", "email", "phone"]
    filterset_class = PatientFilter
    ordering_fields = ['first_name', 'last_name', 'email', 'phone', 'created_at']
    ordering = ['last_name', 'first_name']

    def get_queryset(self):
        queryset = Patient.objects.select_related('clinic')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientListView(LoginRequiredMixin, ListView):
    model = Patient
    template_name = 'patients/list.html'
    context_object_name = 'patients'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        return context

    def get_queryset(self):
        query = self.request.GET.get('q', '').strip()
        user = self.request.user
        queryset = Patient.objects.annotate(appointment_count=Count('appointments')).prefetch_related('appointments')
        if not (user.is_superuser or not user.clinic_id):
            queryset = queryset.filter(clinic=user.clinic)
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )
        return queryset


class PatientDetailView(LoginRequiredMixin, DetailView):
    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        return context

    def get_queryset(self):
        user = self.request.user
        appointment_queryset = Appointment.objects.select_related('service', 'professional__user').order_by('-scheduled_at')
        queryset = Patient.objects.prefetch_related(Prefetch('appointments', queryset=appointment_queryset))
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientCreateView(LoginRequiredMixin, CreateView):
    model = Patient
    form_class = PatientForm
    template_name = 'patients/patient_form.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada.')
            return redirect('patients:list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        context['next_url'] = self.request.GET.get('next', '')
        return context

    def form_valid(self, form):
        try:
            self.object = create_patient(clinic=self.request.user.clinic, **form.cleaned_data)
        except ValueError as exc:
            form.add_error('phone', str(exc))
            return self.form_invalid(form)
        messages.success(self.request, 'Paciente creado correctamente.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        # Si venimos del alta de cita, volvemos a ese formulario con el
        # paciente recién creado preseleccionado por query string.
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            parts = urlparse(next_url)
            query = dict(parse_qsl(parts.query))
            query['patient'] = self.object.pk
            return urlunparse(parts._replace(query=urlencode(query)))
        return reverse_lazy('patients:detail', kwargs={'id': self.object.pk})


class PatientEditView(LoginRequiredMixin, UpdateView):
    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/edit_patient.html'
    form_class = PatientForm

    def get_queryset(self):
        user = self.request.user
        queryset = Patient.objects.select_related('clinic')
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.form_class(instance=self.object)
        context['section'] = 'patients'
        return context
    
    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('patients:detail', kwargs={'id': self.object.pk})
    
