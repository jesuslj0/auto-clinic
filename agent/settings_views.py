"""Sección «Agente WhatsApp» del panel: probar, personalidad, Meta y webhook.

Cada apartado es una URL propia con su formulario y su POST, así un error de
validación en las credenciales no saca a nadie del chat de prueba. Todo es
cosa de administradores de la clínica.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from agent.forms import AgentProfileForm, MetaCredentialsForm, WebhookForm
from agent.models import AgentProfile, ChatMessage
from agent.persona import build_persona_prompt
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

        recent = session.messages.order_by('-created_at')[:self.test_history_limit]
        return [
            {
                'role': 'user' if message.sender == ChatMessage.Sender.PATIENT else 'agent',
                'text': message.body,
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
            # La vista previa es la del perfil GUARDADO: es lo que n8n recibe hoy.
            persona_prompt=build_persona_prompt(AgentProfile.for_clinic(request.user.clinic)),
        )


class AgentMetaSettingsView(AgentSettingsMixin, View):
    """Credenciales de la Cloud API de WhatsApp."""

    template_name = 'agent/settings/meta.html'
    tab = 'meta'

    def get(self, request):
        return self.render_tab(request, form=MetaCredentialsForm(instance=request.user.clinic))

    def post(self, request):
        # La clave del agente no se toca desde aquí: la rota el equipo de la
        # plataforma en el admin, que es quien mantiene el workflow de n8n.
        form = MetaCredentialsForm(request.POST, instance=request.user.clinic)
        if form.is_valid():
            form.save()
            messages.success(request, 'Credenciales de WhatsApp guardadas correctamente.')
            return redirect('agent_settings:meta')
        return self.render_tab(request, form=form)


class AgentWebhookSettingsView(AgentSettingsMixin, View):
    """URL del webhook y token de verificación, los dos valores que se pegan en Meta."""

    template_name = 'agent/settings/webhook.html'
    tab = 'webhook'

    def get(self, request):
        return self._render(request, WebhookForm(instance=request.user.clinic))

    def post(self, request):
        form = WebhookForm(request.POST, instance=request.user.clinic)
        if form.is_valid():
            form.save()
            messages.success(request, 'Token de verificación guardado correctamente.')
            return redirect('agent_settings:webhook')
        return self._render(request, form)

    def _render(self, request, form):
        return self.render_tab(request, form=form, webhook_url=settings.WHATSAPP_WEBHOOK_URL)


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

        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Petición no válida.'}, status=400)

        message = (body.get('message') or '').strip()
        if not message:
            return JsonResponse({'error': 'El mensaje no puede estar vacío.'}, status=400)

        phone = resolve_test_phone(clinic)

        # El mensaje se registra ANTES de llamar a n8n: si el agente falla o
        # tarda, en el panel queda igualmente lo que se le preguntó.
        session = get_or_create_session(clinic, phone, is_test=True)
        record_message(
            clinic=clinic,
            session=session,
            direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT,
            body=message,
            raw={'source': 'panel-test'},
        )
        # Lo acaba de escribir quien está mirando la pantalla, así que no tiene
        # sentido que le aparezca como pendiente de leer.
        mark_session_read(session)

        payload = json.dumps({
            'clinic_id': clinic.clinic_id,
            'phone': phone,
            'message': message,
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
