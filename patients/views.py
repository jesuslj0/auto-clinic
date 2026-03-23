from rest_framework import viewsets

from core.permissions import IsStaffOrAdmin
from patients.models import Patient
from patients.serializers import PatientSerializer


class PatientViewSet(viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['first_name', 'last_name', 'email', 'phone']
    filterset_fields = ['clinic']

    def get_queryset(self):
        queryset = Patient.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
