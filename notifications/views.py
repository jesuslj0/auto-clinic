from rest_framework import viewsets

from core.mixins import ExportMixin
from core.permissions import IsStaffOrAdmin
from notifications.models import Reminder
from notifications.serializers import ReminderSerializer


class ReminderViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ReminderSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['clinic', 'appointment', 'reminder_type', 'success']
    ordering_fields = ['scheduled_for', 'sent_at', 'reminder_type', 'success']
    ordering = ['scheduled_for']

    def get_queryset(self):
        queryset = Reminder.objects.select_related('clinic', 'appointment')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
