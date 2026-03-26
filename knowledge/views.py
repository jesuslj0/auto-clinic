from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin
from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase
from knowledge.serializers import (
    ClinicInfoCacheSerializer,
    ClinicInfoQuerySerializer,
    ClinicKnowledgeBaseSerializer,
)


class ClinicKnowledgeBaseViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = ClinicKnowledgeBaseSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    filterset_fields = ['clinic', 'kb_type', 'active']
    search_fields = ['title', 'content']
    ordering_fields = ['kb_type', 'title', 'created_at', 'updated_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = ClinicKnowledgeBase.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ClinicInfoQueryViewSet(ExportMixin, BulkCreateMixin, viewsets.ModelViewSet):
    serializer_class = ClinicInfoQuerySerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['clinic', 'intent_category']
    search_fields = ['question', 'answer', 'intent_category']
    ordering_fields = ['intent_category', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = ClinicInfoQuery.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ClinicInfoCacheViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ClinicInfoCacheSerializer
    permission_classes = [IsStaffOrAdmin]
    filterset_fields = ['clinic', 'intent_category']
    search_fields = ['normalized_question', 'answer']
    ordering_fields = ['intent_category', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = ClinicInfoCache.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
