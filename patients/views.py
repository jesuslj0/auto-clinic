from django.db.models import Count, Prefetch, Q
from django.views.generic import DetailView, ListView
from rest_framework import viewsets

from appointments.models import Appointment
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsStaffOrAdmin
from patients.filters import PatientFilter
from patients.models import Patient
from patients.serializers import PatientSerializer


class PatientViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ["first_name", "last_name", "email", "phone"]
    filterset_class = PatientFilter
    ordering_fields = ['first_name', 'last_name', 'email', 'phone', 'created_at']
    ordering = ['last_name', 'first_name']

    def get_queryset(self):
        queryset = Patient.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientListView(ListView):
    model = Patient
    template_name = 'patients/list.html'
    context_object_name = 'patients'

    def get_queryset(self):
        query = self.request.GET.get('q', '').strip()
        queryset = Patient.objects.annotate(appointment_count=Count('appointments')).prefetch_related('appointments')
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )
        return queryset


class PatientDetailView(DetailView):
    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/detail.html'

    def get_queryset(self):
        appointment_queryset = Appointment.objects.select_related('service', 'assigned_to').order_by('-scheduled_at')
        return Patient.objects.prefetch_related(Prefetch('appointments', queryset=appointment_queryset))
