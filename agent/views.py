from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from agent.models import AgentMemory, ChatMessage, ConversationSession, WorkflowError
from agent.serializers import (
    AgentMemorySerializer,
    ChatMessageSerializer,
    ConversationSessionSerializer,
    WorkflowErrorSerializer,
)
from agent.services import mark_session_read
from core.authentication import ClinicAgent
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsAgentClinicKey, IsClinicAdminOrReadOnly, IsStaffOrAdmin


def scope_to_clinic(queryset, user):
    """Restringe un queryset a la clínica del solicitante.

    El agente de n8n ve solo la suya. El staff, la suya. Superusuarios y
    usuarios sin clínica asignada ven todo.
    """
    if isinstance(user, ClinicAgent):
        return queryset.filter(clinic=user.clinic)
    if user.is_superuser or not user.clinic_id:
        return queryset
    return queryset.filter(clinic=user.clinic)


class AgentMemoryViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = AgentMemorySerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    filterset_fields = ['session_id']
    search_fields = ['session_id']
    ordering_fields = ['session_id', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return scope_to_clinic(AgentMemory.objects.all(), self.request.user)


class WorkflowErrorViewSet(ExportMixin, viewsets.ModelViewSet):
    """Errores que n8n registra cuando falla un workflow.

    Append-only vía API: n8n los crea con POST usando la `Api-Key` de la clínica
    y el staff los consulta (o los revisa en el admin de Django). No se editan ni
    se borran por la API, así que PUT/PATCH/DELETE quedan fuera y devuelven 405.
    """

    serializer_class = WorkflowErrorSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    http_method_names = ['get', 'post', 'head', 'options']
    filterset_fields = ['workflow', 'phone']
    search_fields = ['workflow', 'workflow_name', 'node_name', 'phone', 'error_message']
    ordering_fields = ['workflow', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return scope_to_clinic(WorkflowError.objects.all(), self.request.user)


class ConversationSessionViewSet(ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    serializer_class = ConversationSessionSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    filterset_fields = ['clinic', 'phone', 'agent_paused']
    search_fields = ['phone']
    ordering_fields = ['phone', 'last_interaction', 'last_message_at', 'updated_at']
    ordering = ['-last_message_at']

    def get_queryset(self):
        queryset = ConversationSession.objects.select_related('clinic', 'patient')
        return scope_to_clinic(queryset, self.request.user)

    @action(detail=True, methods=['get'], url_path='status')
    def get_status(self, request, pk=None):
        session = self.get_object()
        return Response({
            'id': str(session.id),
            'phone': session.phone,
            'last_interaction': session.last_interaction,
            'has_appointment_context': bool(session.appointment_context),
            'agent_paused': session.agent_paused,
            'unread_count': session.unread_count,
            'updated_at': session.updated_at,
        })

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Pone a cero los no leídos del hilo y sella los mensajes entrantes."""
        session = self.get_object()
        mark_session_read(session)
        session.refresh_from_db(fields=['unread_count'])
        return Response({'id': str(session.id), 'unread_count': session.unread_count})


class ChatMessageViewSet(ExportMixin, BulkCreateMixin, viewsets.ModelViewSet):
    """Historial de WhatsApp que alimenta el panel de chats.

    Append-only: n8n publica cada mensaje (entrante y saliente) con POST y el
    staff lo lee. No se edita ni se borra, así que PUT/PATCH/DELETE devuelven
    405 — un hilo de conversación es un registro, no un documento editable.
    """

    serializer_class = ChatMessageSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    http_method_names = ['get', 'post', 'head', 'options']
    filterset_fields = ['session', 'direction', 'sender', 'message_type']
    search_fields = ['body']
    ordering_fields = ['created_at', 'sent_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = ChatMessage.objects.select_related('session', 'clinic')
        return scope_to_clinic(queryset, self.request.user)


# ---------------------------------------------------------------------------
# Panel de chats (vistas de plantilla)
# ---------------------------------------------------------------------------

class ChatInboxView(LoginRequiredMixin, TemplateView):
    """Bandeja de conversaciones de WhatsApp: lista a la izquierda, hilo a la derecha.

    De momento es solo lectura. El tiempo real llegará con un consumer de
    Channels; hasta entonces la página se refresca al navegar.
    """

    template_name = 'agent/chat_inbox.html'

    # Mensajes que se pintan de entrada. El enlace "ver anteriores" amplía la
    # ventana en vez de paginar hacia atrás: en un hilo se lee de abajo arriba.
    default_message_limit = 50
    max_message_limit = 1000

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        query = self.request.GET.get('q', '').strip()
        only_unread = self.request.GET.get('unread') == '1'

        sessions = scope_to_clinic(
            ConversationSession.objects.select_related('patient'), user
        )
        if query:
            sessions = sessions.filter(
                Q(phone__icontains=query)
                | Q(patient__first_name__icontains=query)
                | Q(patient__last_name__icontains=query)
                | Q(last_message_preview__icontains=query)
            )
        if only_unread:
            sessions = sessions.filter(unread_count__gt=0)

        # Las conversaciones sin mensajes (creadas por el bot pero aún mudas)
        # van al final en vez de encabezar la lista.
        sessions = sessions.order_by(F('last_message_at').desc(nulls_last=True), '-updated_at')

        active_session = self._get_active_session(sessions)
        messages_page = []
        has_older_messages = False
        message_limit = self._get_message_limit()

        if active_session is not None:
            total = active_session.messages.count()
            has_older_messages = total > message_limit
            # Se cogen los N más recientes y se le da la vuelta para pintarlos
            # en orden cronológico.
            recent = list(active_session.messages.order_by('-created_at')[:message_limit])
            messages_page = list(reversed(recent))

            # Abrir el hilo lo marca como leído. Es un efecto en un GET, sí,
            # pero es lo que espera cualquiera que use un cliente de chat.
            if active_session.unread_count:
                mark_session_read(active_session)
                active_session.unread_count = 0

        context.update(
            {
                'sessions': sessions,
                'active_session': active_session,
                'chat_messages': messages_page,
                'has_older_messages': has_older_messages,
                'next_message_limit': min(message_limit * 2, self.max_message_limit),
                'query': query,
                'only_unread': only_unread,
                # Se calcula al final, ya descontada la conversación que se
                # acaba de abrir.
                'total_unread': sessions.aggregate(total=Sum('unread_count'))['total'] or 0,
                'section': 'chats',
            }
        )
        return context

    def _get_active_session(self, sessions):
        session_id = self.kwargs.get('session_id')
        if session_id:
            return get_object_or_404(sessions, pk=session_id)
        return None

    def _get_message_limit(self):
        try:
            limit = int(self.request.GET.get('limit', self.default_message_limit))
        except (TypeError, ValueError):
            return self.default_message_limit
        return max(1, min(limit, self.max_message_limit))
