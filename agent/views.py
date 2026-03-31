from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from agent.models import AgentMemory, ConversationSession, WorkflowError
from agent.serializers import (
    AgentMemorySerializer,
    ConversationSessionSerializer,
    WorkflowErrorSerializer,
)
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin


class AgentMemoryViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = AgentMemorySerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['session_id']
    search_fields = ['session_id']
    ordering_fields = ['session_id', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return AgentMemory.objects.all()


class WorkflowErrorViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = WorkflowErrorSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['workflow', 'phone']
    search_fields = ['workflow', 'workflow_name', 'node_name', 'phone', 'error_message']
    ordering_fields = ['workflow', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return WorkflowError.objects.all()


class ConversationSessionViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = ConversationSessionSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['clinic', 'phone']
    search_fields = ['phone']
    ordering_fields = ['phone', 'last_interaction', 'updated_at']
    ordering = ['-updated_at']

    def get_queryset(self):
        queryset = ConversationSession.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    @action(detail=True, methods=['get'], url_path='status')
    def get_status(self, request, pk=None):
        session = self.get_object()
        return Response({
            'id': str(session.id),
            'phone': session.phone,
            'last_interaction': session.last_interaction,
            'has_appointment_context': bool(session.appointment_context),
            'updated_at': session.updated_at,
        })
