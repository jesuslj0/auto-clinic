from rest_framework import viewsets

from billing.models import Subscription
from billing.serializers import SubscriptionSerializer
from core.mixins import ExportMixin
from core.permissions import IsClinicAdminOrReadOnly


class SubscriptionViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    filterset_fields = ['clinic', 'status', 'plan_name']
    ordering_fields = ['plan_name', 'status', 'starts_at', 'ends_at', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = Subscription.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
