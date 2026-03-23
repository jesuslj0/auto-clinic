from rest_framework import viewsets

from core.permissions import IsStaffOrAdmin
from services.models import Service
from services.serializers import ServiceSerializer


class ServiceViewSet(viewsets.ModelViewSet):
    serializer_class = ServiceSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['name', 'description']
    filterset_fields = ['clinic', 'is_active']

    def get_queryset(self):
        queryset = Service.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
