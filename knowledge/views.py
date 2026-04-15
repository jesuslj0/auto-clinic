from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin
from knowledge.forms import KB_TYPE_LABELS, KnowledgeBaseForm
from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase
from knowledge.serializers import (
    ClinicInfoCacheSerializer,
    ClinicInfoQuerySerializer,
    ClinicKnowledgeBaseSerializer,
)


KB_TYPE_DISPLAY = dict(KB_TYPE_LABELS)


class KnowledgeBaseListView(LoginRequiredMixin, ListView):
    model = ClinicKnowledgeBase
    template_name = 'knowledge/knowledge_list.html'
    context_object_name = 'entries'

    def get_queryset(self):
        return ClinicKnowledgeBase.objects.filter(clinic=self.request.user.clinic).order_by('kb_type', 'title')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        grouped = {}
        for entry in context['entries']:
            label = KB_TYPE_DISPLAY.get(entry.kb_type, entry.kb_type)
            grouped.setdefault(label, []).append(entry)
        context['grouped_entries'] = grouped
        context['section'] = 'knowledge'
        return context


class KnowledgeBaseCreateView(LoginRequiredMixin, CreateView):
    model = ClinicKnowledgeBase
    form_class = KnowledgeBaseForm
    template_name = 'knowledge/knowledge_form.html'
    success_url = reverse_lazy('knowledge:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'knowledge'
        return context

    def form_valid(self, form):
        form.instance.clinic = self.request.user.clinic
        messages.success(self.request, 'Entrada creada correctamente.')
        return super().form_valid(form)


class KnowledgeBaseEditView(LoginRequiredMixin, UpdateView):
    model = ClinicKnowledgeBase
    form_class = KnowledgeBaseForm
    template_name = 'knowledge/knowledge_form.html'
    success_url = reverse_lazy('knowledge:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'knowledge'
        return context

    def get_queryset(self):
        return ClinicKnowledgeBase.objects.filter(clinic=self.request.user.clinic)

    def form_valid(self, form):
        messages.success(self.request, 'Entrada actualizada correctamente.')
        return super().form_valid(form)


class KnowledgeBaseDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        entry = get_object_or_404(ClinicKnowledgeBase, pk=pk, clinic=request.user.clinic)
        entry.delete()
        messages.success(request, 'Entrada eliminada correctamente.')
        return redirect('knowledge:list')


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
