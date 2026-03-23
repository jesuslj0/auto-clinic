from rest_framework import viewsets

from core.permissions import IsStaffOrAdmin
from notifications.models import Reminder
from notifications.serializers import ReminderSerializer


class ReminderViewSet(viewsets.ModelViewSet):
    serializer_class = ReminderSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['clinic', 'appointment', 'reminder_type', 'success']

    def get_queryset(self):
        queryset = Reminder.objects.select_related('clinic', 'appointment')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
