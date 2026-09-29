"""Sección «Agente WhatsApp» del panel: chat de prueba, personalidad y configuración.

Cada apartado es una URL propia con su formulario y su POST, así un error de
validación en las credenciales no saca a nadie del chat de prueba. Todo es
cosa de administradores de la clínica.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from agent.forms import AgentProfileForm, MetaCredentialsForm, WebhookForm
from agent.media import attach_media
from agent.models import AgentProfile, ChatMessage
from agent.services import (
    get_or_create_session,
    get_test_session,
    mark_session_read,
    record_message,
    resolve_test_phone,
)
from core.mixins import ClinicAdminRequiredMixin


class AgentSettingsMixin(ClinicAdminRequiredMixin):
    """Clínica del usuario, estado de la conexión y la pestaña activa."""

    template_name = None
    tab = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.clinic is None:
            messages.error(request, 'Tu usuario no está asociado a ninguna clínica.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def render_tab(self, request, **extra):
        clinic = request.user.clinic
        context = {
            'clinic_obj': clinic,
            'section': 'agent',
            'agent_tab': self.tab,
            'has_token': bool(clinic.whatsapp_token),
            'has_credentials': bool(clinic.whatsapp_phone_number_id and clinic.whatsapp_token),
            'is_connected': bool(
                clinic.whatsapp_phone_number_id
                and clinic.whatsapp_token
                and clinic.whatsapp_verify_token
            ),
            **extra,
        }
        return render(request, self.template_name, context)


class AgentTestView(AgentSettingsMixin, View):
    """Página de entrada: el chat de prueba, con el estado y el perfil al lado."""

    template_name = 'agent/settings/test.html'
    tab = 'test'

    # Mensajes del chat de pruebas que se precargan al abrir la página.
    test_history_limit = 50

    def get(self, request):
        clinic = request.user.clinic
        return self.render_tab(
            request,
            profile=AgentProfile.for_clinic(clinic),
            test_messages=self._test_history(clinic),
        )

    def _test_history(self, clinic):
        """Hilo de pruebas ya guardado, en el formato que pinta el chat del panel.

        El chat es la única ventana a esta conversación —queda fuera de la
        bandeja de chats—, así que se recupera al recargar en vez de empezar
        siempre en blanco.
        """
        session = get_test_session(clinic)
        if session is None:
            return []

        recent = (
            session.messages.select_related('attachment')
            .order_by('-created_at')[:self.test_history_limit]
        )
        return [
            {
                'role': 'user' if message.sender == ChatMessage.Sender.PATIENT else 'agent',
                'text': message.display_body,
                # Solo el enlace a la vista protegida (permiso + AccessLog), nunca
                # la URL firmada: la foto se abre cuando alguien la pide.
                'image_url': (
                    reverse('agent:chat-media', args=[message.pk])
                    if message.media is not None else ''
                ),
                'time': timezone.localtime(
                    message.sent_at or message.created_at
                ).strftime('%H:%M'),
            }
            for message in reversed(list(recent))
        ]


class AgentPersonaSettingsView(AgentSettingsMixin, View):
    """Nombre, tono y presentación del agente, con el bloque de prompt resultante."""

    template_name = 'agent/settings/persona.html'
    tab = 'persona'

    def get(self, request):
        profile = AgentProfile.for_clinic(request.user.clinic)
        return self._render(request, AgentProfileForm(instance=profile), profile)

    def post(self, request):
        profile = AgentProfile.for_clinic(request.user.clinic)
        form = AgentProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.updated_by = request.user
            profile.save()
            messages.success(
                request,
                'Personalidad guardada. El agente la usará desde el próximo mensaje.',
            )
            return redirect('agent_settings:persona')
        return self._render(request, form, profile)

    def _render(self, request, form, profile):
        return self.render_tab(
            request,
            form=form,
            profile=profile,
            # El aviso de versión es el del perfil GUARDADO: es lo que n8n recibe
            # hoy, aunque el formulario vuelva con errores y cambios sin guardar.
            saved_profile=AgentProfile.for_clinic(request.user.clinic),
        )


class AgentConnectionSettingsView(AgentSettingsMixin, View):
    """Conexión con Meta: credenciales de la Cloud API y webhook, en una página.

    Son dos formularios con su propio botón; el campo oculto `form` dice cuál se
    envió. Solo se valida ese: el otro se pinta con lo guardado, así un error en
    las credenciales no borra lo que se estaba escribiendo en el webhook ni al
    revés.
    """

    template_name = 'agent/settings/config.html'
    tab = 'config'

    def get(self, request):
        return self._render(request)

    def post(self, request):
        clinic = request.user.clinic
        if request.POST.get('form') == 'webhook':
            form = WebhookForm(request.POST, instance=clinic)
            if form.is_valid():
                form.save()
                messages.success(request, 'Token de verificación guardado correctamente.')
                return redirect('agent_settings:config')
            return self._render(request, webhook_form=form)

        # La clave del agente no se toca desde aquí: la rota el equipo de la
        # plataforma en el admin, que es quien mantiene el workflow de n8n.
        form = MetaCredentialsForm(request.POST, instance=clinic)
        if form.is_valid():
            form.save()
            messages.success(request, 'Credenciales de WhatsApp guardadas correctamente.')
            return redirect('agent_settings:config')
        return self._render(request, meta_form=form)

    def _render(self, request, meta_form=None, webhook_form=None):
        clinic = request.user.clinic
        return self.render_tab(
            request,
            meta_form=meta_form or MetaCredentialsForm(instance=clinic),
            webhook_form=webhook_form or WebhookForm(instance=clinic),
            webhook_url=settings.WHATSAPP_WEBHOOK_URL,
        )


class AgentTestMessageView(ClinicAdminRequiredMixin, View):
    """Proxy hacia el webhook de prueba de n8n.

    Recibe un mensaje del panel, lo reenvía a n8n con las credenciales del
    lado servidor (nunca se exponen en el navegador) y devuelve la respuesta
    del agente como JSON.

    El intercambio se registra en el historial de chats igual que una
    conversación real, para poder revisar después qué se probó y qué contestó
    el agente.
    """

    def post(self, request):
        clinic = request.user.clinic
        if clinic is None:
            return JsonResponse({'error': 'Tu usuario no está asociado a ninguna clínica.'}, status=400)

        message, image = self._read_input(request)
        if message is None:
            return JsonResponse({'error': 'Petición no válida.'}, status=400)
        if not message and image is None:
            return JsonResponse({'error': 'El mensaje no puede estar vacío.'}, status=400)

        phone = resolve_test_phone(clinic)

        # El mensaje se registra ANTES de llamar a n8n: si el agente falla o
        # tarda, en el panel queda igualmente lo que se le preguntó.
        session = get_or_create_session(clinic, phone, is_test=True)
        try:
            # Una foto que no pasa la validación no deja mensaje huérfano en el
            # hilo: registro y adjunto van en la misma transacción.
            with transaction.atomic():
                inbound = record_message(
                    clinic=clinic,
                    session=session,
                    direction=ChatMessage.Direction.INBOUND,
                    sender=ChatMessage.Sender.PATIENT,
                    # Sin pie de foto, el mismo marcador que pone n8n («[image]»).
                    body=message or ('[image]' if image is not None else ''),
                    message_type=(
                        ChatMessage.MessageType.IMAGE if image is not None
                        else ChatMessage.MessageType.TEXT
                    ),
                    raw={'source': 'panel-test'},
                )
                if image is not None:
                    # Mismo camino que una foto de WhatsApp: validada por
                    # contenido, sin metadatos y al bucket privado.
                    attach_media(inbound, image)
        except ValidationError as exc:
            return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
        # Lo acaba de escribir quien está mirando la pantalla, así que no tiene
        # sentido que le aparezca como pendiente de leer.
        mark_session_read(session)

        # A n8n solo le llega QUE hay una foto (y su pie), nunca la imagen: el
        # agente acusa recibo y la clínica la revisa en el panel.
        payload = json.dumps({
            'clinic_id': clinic.clinic_id,
            'phone': phone,
            'message': message,
            'message_type': 'image' if image is not None else 'text',
        }).encode('utf-8')

        # n8n autentica con la agent_api_key de la clínica (mismo esquema que
        # AgentClinicKeyAuthentication); el clinic_id viaja también en el body.
        req = urllib.request.Request(
            settings.WHATSAPP_TEST_WEBHOOK_URL,
            data=payload,
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Api-Key {clinic.agent_api_key}',
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                error = 'n8n rechazó la clave del agente de esta clínica (no autorizada).'
            elif exc.code == 404:
                error = 'El webhook de prueba no existe en n8n. Comprueba que esté activo.'
            else:
                error = f'n8n respondió {exc.code}. Revisa el webhook de prueba.'
            return JsonResponse({'error': error}, status=502)
        except urllib.error.URLError:
            return JsonResponse(
                {'error': 'No se pudo contactar con n8n. Comprueba la URL del webhook de prueba.'},
                status=502,
            )

        # n8n puede devolver texto plano o JSON con distintas claves.
        reply = raw
        debounced = False
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                # n8n agrupa los mensajes que llegan seguidos y contesta solo al
                # último: los descartados cierran su petición sin respuesta.
                debounced = bool(data.get('debounced'))
                reply = data.get('reply') or data.get('text') or data.get('message') or data.get('output') or raw
        except (json.JSONDecodeError, TypeError):
            pass

        # Se comprueba antes de mirar `reply`: en un mensaje descartado viene
        # vacía y, como '' es falsy, el encadenado de arriba habría acabado
        # devolviendo el JSON crudo como si fuera la respuesta del agente.
        if debounced:
            return JsonResponse({'reply': '', 'debounced': True})

        reply = (reply or '').strip()
        if reply:
            record_message(
                clinic=clinic,
                session=session,
                direction=ChatMessage.Direction.OUTBOUND,
                sender=ChatMessage.Sender.AGENT,
                body=reply,
                status=ChatMessage.Status.SENT,
                raw={'source': 'panel-test'},
            )
        else:
            reply = 'El agente no devolvió ninguna respuesta.'

        return JsonResponse({'reply': reply})

    @staticmethod
    def _read_input(request):
        """(texto, foto) del mensaje de prueba; texto `None` si la petición no vale.

        Texto solo llega como JSON; con foto, como multipart (`file` + `message`,
        que hace de pie de foto).
        """
        if request.content_type == 'multipart/form-data':
            return (request.POST.get('message') or '').strip(), request.FILES.get('file')
        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return None, None
        if not isinstance(body, dict):
            return None, None
        return (body.get('message') or '').strip(), None
