import uuid

from django.contrib import messages as django_messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import F, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from agent.media import MediaAlreadyAttached, attach_media, log_media_view, signed_media_url
from agent.models import AgentMemory, ChatAttachment, ChatMessage, ConversationSession, WorkflowError
from agent.serializers import (
    AgentMemorySerializer,
    ChatMessageSerializer,
    ConversationSessionSerializer,
    DeliveryStatusSerializer,
    PlatformWorkflowErrorSerializer,
    WorkflowErrorSerializer,
)
from agent.realtime import broadcast_clinic, broadcast_session, clinic_unread_total
from agent.services import apply_delivery_status, mark_session_read, send_staff_message
from agent.whatsapp import WhatsAppError
from core.authentication import ClinicAgent
from core.mixins import ExportMixin
from core.permissions import IsAgentClinicKey, IsAgentErrorsKey, IsClinicAdminOrReadOnly, IsStaffOrAdmin


def scope_to_clinic(queryset, user):
    """Restringe un queryset a la clínica del solicitante.

    El agente de n8n ve solo la suya. El staff, la suya. Un superusuario con
    clínica asignada queda acotado a ella igual que el staff; solo los usuarios
    SIN clínica (el equipo de plataforma) ven todo.
    """
    if isinstance(user, ClinicAgent):
        return queryset.filter(clinic=user.clinic)
    if not user.clinic_id:
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


class PlatformWorkflowErrorView(APIView):
    """Registro de errores del manejador global de n8n (Error Trigger).

    Ese manejador no conoce la clínica de la ejecución fallida, así que no puede
    usar la Api-Key de clínica de `/api/agent/errors/`. Usa AGENT_ERRORS_API_KEY,
    que solo permite este POST. Sin autenticación de DRF a propósito: la
    `AgentClinicKeyAuthentication` rechazaría con 401 cualquier `Api-Key` que no
    sea de una clínica antes de llegar al permiso (igual que `AgentConfigView`).
    """

    permission_classes = [IsAgentErrorsKey]
    authentication_classes = []

    def post(self, request):
        serializer = PlatformWorkflowErrorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=http_status.HTTP_201_CREATED)


class ConversationSessionViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ConversationSessionSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    filterset_fields = ['clinic', 'phone', 'agent_paused']
    search_fields = ['phone']
    ordering_fields = ['phone', 'last_interaction', 'last_message_at', 'updated_at']
    ordering = ['-last_message_at']

    def get_queryset(self):
        queryset = ConversationSession.objects.select_related('clinic', 'patient')
        return scope_to_clinic(queryset, self.request.user)

    # Campos editables por la API que cambian lo que pinta la bandeja. No está
    # `last_interaction`: n8n lo reescribe dos veces por mensaje y el aviso de ese
    # mensaje ya lo ha emitido `record_message()`.
    broadcast_fields = {'agent_paused', 'patient', 'phone', 'last_staff_message_at'}

    def perform_update(self, serializer):
        super().perform_update(serializer)
        # n8n puede pausar un hilo por aquí; la bandeja tiene que enterarse.
        if self.broadcast_fields & serializer.validated_data.keys():
            broadcast_session(serializer.instance)

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

    @action(detail=False, methods=['get'], url_path='should-reply')
    def should_reply(self, request):
        """Le dice a n8n si el agente debe contestar en un hilo, y con qué contexto.

        n8n consulta por teléfono porque es lo único que trae el webhook de
        Meta. Devuelve además los últimos mensajes para que, al retomar tras una
        intervención humana, el agente sepa lo que dijo la recepcionista; su
        propia memoria no lo contiene porque ese mensaje no pasó por n8n.
        """
        phone = (request.query_params.get('phone') or '').strip()
        if not phone:
            return Response(
                {'detail': 'El parámetro phone es obligatorio.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            history_size = int(request.query_params.get('history', 20))
        except (TypeError, ValueError):
            history_size = 20
        history_size = max(0, min(history_size, 100))

        session = self.get_queryset().filter(phone=phone).first()
        if session is None:
            # Primer mensaje de este número: no hay hilo todavía, así que no hay
            # nada que pause al agente. Se responde sin crear la sesión, que ya
            # la creará la ingesta del mensaje.
            clinic = getattr(request.user, 'clinic', None)
            return Response({
                'session_id': None,
                'phone': phone,
                'agent_should_reply': bool(clinic and clinic.agent_enabled),
                'agent_enabled': bool(clinic and clinic.agent_enabled),
                'agent_paused': False,
                'handoff_active': False,
                'handoff_expires_at': None,
                'history': [],
            })

        history = []
        if history_size:
            recent = session.messages.order_by('-created_at')[:history_size]
            history = [
                {
                    'sender': message.sender,
                    'body': message.body,
                    'created_at': (message.sent_at or message.created_at).isoformat(),
                }
                for message in reversed(list(recent))
            ]

        return Response({
            'session_id': str(session.id),
            'phone': session.phone,
            'agent_should_reply': session.agent_should_reply,
            'agent_enabled': session.clinic.agent_enabled if session.clinic else False,
            'agent_paused': session.agent_paused,
            'handoff_active': session.is_handoff_active,
            'handoff_expires_at': (
                session.handoff_expires_at.isoformat() if session.handoff_expires_at else None
            ),
            'history': history,
        })


class ChatMessageViewSet(ExportMixin, viewsets.ModelViewSet):
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

    @action(
        detail=False,
        methods=['post'],
        url_path='status',
        permission_classes=[IsAgentClinicKey],
    )
    def delivery_status(self, request):
        """Acuses de entrega de WhatsApp (✓, ✓✓, leído, fallido). Solo n8n.

        Acepta un acuse o una lista: `{wa_message_id, status, timestamp?, error?}`.
        Un acuse de un mensaje que no es de la clínica de la `Api-Key` (o que no
        existe) no es un error: n8n reenvía todos los de Meta, incluidos los de
        mensajes que no pasaron por aquí. Se responde `matched: false`.

        Es un endpoint propio y no un `PATCH` sobre el mensaje a propósito: el
        hilo es de solo inserción y esto solo toca los campos del acuse.
        """
        many = isinstance(request.data, list)
        serializer = DeliveryStatusSerializer(data=request.data, many=many)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data if many else [serializer.validated_data]

        results = []
        for item in items:
            message = apply_delivery_status(
                clinic=request.user.clinic,
                wa_message_id=item['wa_message_id'],
                status=item['status'],
                timestamp=item.get('timestamp'),
                error=item.get('error', ''),
            )
            results.append({
                'wa_message_id': item['wa_message_id'],
                'matched': message is not None,
                'status': message.status if message is not None else None,
            })
        return Response(results if many else results[0])

    @action(
        detail=True,
        methods=['post'],
        url_path='media',
        permission_classes=[IsAgentClinicKey],
        parser_classes=[MultiPartParser],
    )
    def media(self, request, pk=None):
        """Sube la foto o nota de voz de un mensaje entrante. Una sola vez.

        Solo n8n (`Api-Key` de la clínica): descarga el binario de Meta y lo
        manda aquí en el campo `file`. Un mensaje de otra clínica da 404.

        - 201: guardado. La respuesta describe el fichero, nunca trae una URL.
        - 400: el mensaje no admite adjunto o el fichero no pasa la validación
          (se mira el contenido, no la extensión).
        - 409: el mensaje ya tenía adjunto. No se reemplaza nunca.
        """
        message = self.get_object()
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response(
                {'file': 'Falta el fichero (campo multipart `file`).'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        try:
            attachment = attach_media(message, uploaded)
        except MediaAlreadyAttached:
            return Response(
                {'detail': 'Este mensaje ya tiene adjunto y no se puede reemplazar.'},
                status=http_status.HTTP_409_CONFLICT,
            )
        except ValidationError as exc:
            return Response({'file': exc.messages}, status=http_status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                'id': str(attachment.id),
                'message': str(message.id),
                'kind': attachment.kind,
                'mime_type': attachment.mime_type,
                'size_bytes': attachment.size_bytes,
                'checksum': attachment.checksum,
            },
            status=http_status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Panel de chats (vistas de plantilla)
# ---------------------------------------------------------------------------

def read_inbox_filters(request):
    """Filtros de la lista de conversaciones: (texto buscado, solo sin leer)."""
    return request.GET.get('q', '').strip(), request.GET.get('unread') == '1'


def inbox_sessions(user, *, query='', only_unread=False):
    """Conversaciones de la bandeja, filtradas y en el orden en que se pintan.

    La usan la página entera y el fragmento que se refresca en vivo, para que
    los dos enseñen exactamente la misma lista.
    """
    # El hilo del cliente de prueba vive en la configuración del agente:
    # sus mensajes se guardan igual, pero no son conversaciones reales y
    # ensucian la bandeja (y el contador de no leídos) de la clínica.
    sessions = scope_to_clinic(
        ConversationSession.objects.select_related('patient', 'clinic'), user
    ).exclude(is_test=True)
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
    return sessions.order_by(F('last_message_at').desc(nulls_last=True), '-updated_at')


def total_unread(user):
    """No leídos de todas las conversaciones de la bandeja, sin filtros."""
    if user.clinic_id:
        return clinic_unread_total(user.clinic_id)
    return inbox_sessions(user).aggregate(total=Sum('unread_count'))['total'] or 0


def mark_day_starts(messages, previous=None):
    """Marca `starts_day` en los mensajes que abren un día nuevo en el hilo.

    `previous` es el último mensaje que ya está pintado: si la tanda sigue en
    su mismo día, el primero no lleva separador.
    """
    last_day = timezone.localtime(previous.created_at).date() if previous else None
    for message in messages:
        day = timezone.localtime(message.created_at).date()
        message.starts_day = day != last_day
        last_day = day
    return messages


class ChatInboxView(LoginRequiredMixin, TemplateView):
    """Bandeja de conversaciones de WhatsApp: lista a la izquierda, hilo a la derecha.

    Se actualiza en vivo: `static/js/chat_inbox.js` escucha el socket de chats y
    pide los fragmentos (`ChatMessagesFragmentView`, `ChatSessionListFragmentView`).
    Sin JavaScript sigue funcionando, recargando al navegar.
    """

    template_name = 'agent/chat_inbox.html'

    # Mensajes que se pintan de entrada. El enlace "ver anteriores" amplía la
    # ventana en vez de paginar hacia atrás: en un hilo se lee de abajo arriba.
    default_message_limit = 50
    max_message_limit = 1000

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        query, only_unread = read_inbox_filters(self.request)
        sessions = inbox_sessions(user, query=query, only_unread=only_unread)

        active_session = self._get_active_session(sessions)
        messages_page = []
        has_older_messages = False
        message_limit = self._get_message_limit()

        if active_session is not None:
            total = active_session.messages.count()
            has_older_messages = total > message_limit
            # Se cogen los N más recientes y se le da la vuelta para pintarlos
            # en orden cronológico.
            recent = list(
                active_session.messages.select_related('attachment', 'session__patient')
                .order_by('-created_at', '-id')[:message_limit]
            )
            messages_page = mark_day_starts(list(reversed(recent)))

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
                # acaba de abrir. Sin filtros: buscar no cambia cuántos hay.
                'total_unread': total_unread(user),
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


class ChatSessionActionMixin(LoginRequiredMixin):
    """Resuelve el hilo comprobando que sea de la clínica de quien lo pide."""

    def get_session(self, session_id):
        # Mismo criterio que la bandeja: si el hilo de pruebas no se lista, sus
        # acciones (enviar, pausar el agente) tampoco se atienden por URL suelta.
        sessions = scope_to_clinic(
            ConversationSession.objects.select_related('clinic'), self.request.user
        ).exclude(is_test=True)
        return get_object_or_404(sessions, pk=session_id)


class ChatMessagesFragmentView(ChatSessionActionMixin, View):
    """Burbujas de un hilo en HTML, para añadirlas sin recargar la página.

    `?after=<message_id>` devuelve lo posterior a ese mensaje en orden
    cronológico; sin él, los últimos mensajes. El cliente pide siempre «lo que
    hay después del último que tengo», así que un aviso perdido por el socket
    (wifi caída, móvil suspendido, Daphne reiniciado) se recupera en la
    siguiente petición y no deja huecos en la conversación.

    Si hay más de `max_batch` pendientes, `X-Has-More: 1` pide al cliente que
    repita con el último id recibido. Un `after` que no es de este hilo da 400:
    el cliente debe recargar el hilo entero, no adivinar dónde estaba.
    """

    # Un fetch no debe recibir la página de login como si fueran burbujas.
    raise_exception = True

    initial_limit = ChatInboxView.default_message_limit
    max_batch = 200

    def get(self, request, session_id):
        session = self.get_session(session_id)
        messages_qs = session.messages.select_related('attachment', 'session__patient')

        # Una burbuja concreta, ya pintada, que ha cambiado (le ha llegado la
        # foto, o su estado de entrega). Se devuelve sola para reemplazarla.
        only_id = request.GET.get('only')
        if only_id:
            try:
                message = messages_qs.get(pk=only_id)
            except (ChatMessage.DoesNotExist, ValidationError):
                return HttpResponseBadRequest('El mensaje no pertenece a este hilo.')
            html = render_to_string('agent/_message_bubble.html', {'message': message}, request=request)
            response = HttpResponse(html)
            response['Cache-Control'] = 'no-store'
            return response

        previous = None
        has_more = False
        after_id = request.GET.get('after')
        if after_id:
            try:
                previous = messages_qs.get(pk=after_id)
            except (ChatMessage.DoesNotExist, ValidationError):
                return HttpResponseBadRequest('El mensaje de referencia no pertenece a este hilo.')
            # created_at + id como desempate: dos mensajes en el mismo
            # microsegundo no deben perderse ni repetirse.
            batch = list(
                messages_qs.filter(
                    Q(created_at__gt=previous.created_at)
                    | Q(created_at=previous.created_at, id__gt=previous.id)
                ).order_by('created_at', 'id')[: self.max_batch + 1]
            )
            has_more = len(batch) > self.max_batch
            batch = batch[: self.max_batch]
        else:
            recent = list(messages_qs.order_by('-created_at', '-id')[: self.initial_limit])
            batch = list(reversed(recent))

        mark_day_starts(batch, previous)

        # Quien pide las burbujas tiene el hilo abierto delante: lo que llega
        # está leído, igual que al abrirlo.
        if session.unread_count:
            mark_session_read(session)

        html = render_to_string('agent/_message_run.html', {'messages': batch}, request=request)
        response = HttpResponse(html)
        response['X-Has-More'] = '1' if has_more else '0'
        response['Cache-Control'] = 'no-store'
        return response


class ChatSessionListFragmentView(LoginRequiredMixin, View):
    """Lista de conversaciones en HTML, con los mismos filtros que la bandeja.

    `?active=<session_id>` marca la fila del hilo abierto. `X-Total-Unread`
    lleva el total de no leídos de la clínica, sin filtros, para el contador.
    """

    raise_exception = True

    def get(self, request):
        query, only_unread = read_inbox_filters(request)
        sessions = inbox_sessions(request.user, query=query, only_unread=only_unread)

        try:
            active_session_id = uuid.UUID(request.GET.get('active', ''))
        except ValueError:
            active_session_id = None

        html = render_to_string(
            'agent/_session_list.html',
            {
                'sessions': sessions,
                'query': query,
                'only_unread': only_unread,
                'active_session_id': active_session_id,
            },
            request=request,
        )
        response = HttpResponse(html)
        response['X-Total-Unread'] = str(total_unread(request.user))
        response['Cache-Control'] = 'no-store'
        return response


class ChatMediaView(LoginRequiredMixin, View):
    """Abre la foto o el audio de un mensaje: comprueba, registra y redirige.

    Solo sesión del staff de la clínica del hilo (ver `agent.media`). El orden
    es el único posible: permiso (403) → `AccessLog` → redirección a una URL
    firmada de vida corta. Django no sirve el fichero; lo entrega el bucket
    privado, que además responde con `no-store` para que no quede en caché.
    """

    raise_exception = True

    def get(self, request, message_id):
        attachment = get_object_or_404(
            ChatAttachment.objects.select_related('message__session__patient'),
            message_id=message_id,
        )
        url = signed_media_url(attachment, request.user)
        log_media_view(attachment, request=request)

        response = HttpResponseRedirect(url)
        # La redirección lleva una URL firmada: que no la guarde el navegador,
        # ni un proxy, ni viaje como `Referer` a ninguna parte.
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class ChatSendMessageView(ChatSessionActionMixin, View):
    """Envía un mensaje escrito por el staff desde el panel."""

    def post(self, request, session_id):
        session = self.get_session(session_id)
        body = (request.POST.get('body') or '').strip()

        if not body:
            django_messages.error(request, 'Escribe un mensaje antes de enviarlo.')
        else:
            try:
                send_staff_message(session=session, body=body)
            except WhatsAppError as exc:
                django_messages.error(request, str(exc))

        return redirect('agent:chat-thread', session_id=session.id)


class ChatToggleAgentView(ChatSessionActionMixin, View):
    """Alterna entre «Agente IA» y «Modo humano» en un hilo concreto."""

    def post(self, request, session_id):
        session = self.get_session(session_id)

        session.agent_paused = not session.agent_paused
        if session.agent_paused:
            # Una pausa explícita no debe caducar por inactividad: si alguien
            # pone el hilo en modo humano, se queda así hasta que lo devuelva.
            session.last_staff_message_at = None
            django_messages.success(request, 'Modo humano activado. El agente no responderá en este chat.')
        else:
            django_messages.success(request, 'El agente vuelve a atender este chat.')
        session.save(update_fields=['agent_paused', 'last_staff_message_at', 'updated_at'])
        broadcast_session(session)

        return redirect('agent:chat-thread', session_id=session.id)


class ClinicAgentSwitchView(LoginRequiredMixin, View):
    """Interruptor general del agente para toda la clínica."""

    def post(self, request):
        clinic = request.user.clinic
        if clinic is None:
            django_messages.error(request, 'Tu usuario no está asociado a ninguna clínica.')
            return redirect('agent:chat-inbox')

        clinic.agent_enabled = not clinic.agent_enabled
        clinic.save(update_fields=['agent_enabled'])
        broadcast_clinic(clinic)

        if clinic.agent_enabled:
            django_messages.success(request, 'Agente activado. Volverá a responder los chats de la clínica.')
        else:
            django_messages.success(
                request,
                'Agente desactivado. Ningún chat recibirá respuestas automáticas hasta que lo vuelvas a activar.',
            )

        # El destino viene del formulario, así que se comprueba que sea interno
        # antes de redirigir: si no, sirve para mandar al usuario fuera del sitio.
        next_url = request.POST.get('next') or ''
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect('agent:chat-inbox')
