"""El probador del agente (panel → Agente WhatsApp) deja rastro en el historial."""
import json
import re
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession


@contextmanager
def _n8n_replies(payload):
    """Simula la respuesta del webhook de prueba de n8n."""
    class _Response:
        def read(self):
            return payload.encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch('urllib.request.urlopen', return_value=_Response()):
        yield


@contextmanager
def _n8n_fails():
    error = urllib.error.URLError('sin conexión')
    with patch('urllib.request.urlopen', side_effect=error):
        yield


def _send(client, message="¿Tenéis hueco mañana?"):
    return client.post(
        reverse('agent_settings:test-send'),
        data=json.dumps({'message': message}),
        content_type='application/json',
    )


@pytest.mark.django_db
class TestAgentTestMessageHistory:
    def test_records_both_sides_of_the_exchange(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies(json.dumps({'reply': 'Sí, a las 10:00'})):
            response = _send(client)

        assert response.status_code == 200
        assert response.json()['reply'] == 'Sí, a las 10:00'

        session = ConversationSession.objects.get(clinic=clinic_a)
        messages = list(session.messages.order_by('created_at'))
        assert [(m.direction, m.sender, m.body) for m in messages] == [
            ('inbound', 'patient', '¿Tenéis hueco mañana?'),
            ('outbound', 'agent', 'Sí, a las 10:00'),
        ]

    def test_marks_the_exchange_as_source_panel_test(self, client, admin_user):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        assert all(m.raw == {'source': 'panel-test'} for m in ChatMessage.objects.all())

    def test_does_not_leave_unread(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.unread_count == 0

    def test_incoming_survives_an_n8n_failure(self, client, admin_user, clinic_a):
        """Si el agente no responde, la pregunta tiene que quedar registrada igual."""
        client.force_login(admin_user)
        with _n8n_fails():
            response = _send(client)

        assert response.status_code == 502
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.messages.count() == 1
        assert session.messages.get().direction == 'inbound'

    def test_empty_reply_is_not_recorded_as_agent_message(self, client, admin_user, clinic_a):
        """No inventamos una burbuja del agente si no dijo nada."""
        client.force_login(admin_user)
        with _n8n_replies(''):
            response = _send(client)

        assert response.json()['reply'] == 'El agente no devolvió ninguna respuesta.'
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.messages.count() == 1

    def test_debounced_message_is_not_an_empty_answer(self, client, admin_user, clinic_a):
        """n8n agrupa las ráfagas: contesta el último mensaje, no este.

        No es lo mismo que una respuesta vacía. Aquí no hay nada que enseñar
        porque la respuesta llegará por la petición del último mensaje, así que
        el panel no debe pintar «el agente no devolvió ninguna respuesta».
        """
        client.force_login(admin_user)
        with _n8n_replies(json.dumps({'reply': '', 'debounced': True})):
            response = _send(client)

        assert response.json() == {'reply': '', 'debounced': True}

        # El entrante sí queda registrado: lo escribió el paciente, aunque la
        # respuesta la genere la ejecución del último mensaje de la ráfaga.
        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.messages.count() == 1
        assert session.messages.get().direction == 'inbound'

    def test_uses_test_patient_phone_and_links_the_record(
        self, client, admin_user, clinic_a, patient_a
    ):
        clinic_a.test_patient = patient_a
        clinic_a.save(update_fields=['test_patient'])
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)

        session = ConversationSession.objects.get(clinic=clinic_a)
        assert session.phone == patient_a.phone
        assert session.patient_id == patient_a.pk

    def test_falls_back_to_panel_test_without_test_patient(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        assert ConversationSession.objects.get(clinic=clinic_a).phone == 'panel-test'

    def test_reuses_the_same_thread_across_messages(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Uno'):
            _send(client, "Primera")
        with _n8n_replies('Dos'):
            _send(client, "Segunda")

        assert ConversationSession.objects.filter(clinic=clinic_a).count() == 1
        assert ChatMessage.objects.count() == 4

    def test_empty_message_records_nothing(self, client, admin_user):
        client.force_login(admin_user)
        response = _send(client, "   ")
        assert response.status_code == 400
        assert ChatMessage.objects.count() == 0


@pytest.mark.django_db
class TestTestThreadIsSeparateFromTheInbox:
    """El cliente de prueba se guarda, pero no es un paciente de la clínica."""

    def test_thread_is_flagged_as_test(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        assert ConversationSession.objects.get(clinic=clinic_a).is_test is True

    def test_thread_stays_out_of_the_chat_inbox(self, client, admin_user, clinic_a, patient_a):
        """Ni en la lista de conversaciones ni en el contador de no leídos."""
        clinic_a.test_patient = patient_a
        clinic_a.save(update_fields=['test_patient'])
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)

        response = client.get(reverse('agent:chat-inbox'))
        assert list(response.context['sessions']) == []
        assert response.context['total_unread'] == 0

    def test_thread_cannot_be_opened_from_the_inbox(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        with _n8n_replies('Hola'):
            _send(client)
        session = ConversationSession.objects.get(clinic=clinic_a)

        response = client.get(reverse('agent:chat-thread', args=[session.id]))
        assert response.status_code == 404

    def test_history_is_preloaded_in_the_settings_chat(self, client, admin_user):
        """El chat de configuración es la única ventana a este hilo."""
        client.force_login(admin_user)
        with _n8n_replies(json.dumps({'reply': 'Sí, a las 10:00'})):
            _send(client)

        history = client.get(reverse('agent_settings:test')).context['test_messages']

        assert [(entry['role'], entry['text']) for entry in history] == [
            ('user', '¿Tenéis hueco mañana?'),
            ('agent', 'Sí, a las 10:00'),
        ]
        # Cada mensaje lleva su hora local para pintarla en la burbuja. Se
        # comprueba el formato, no el valor: el reloj no es parte del contrato.
        assert all(re.fullmatch(r'\d{2}:\d{2}', entry['time']) for entry in history)

    def test_settings_chat_starts_empty_without_history(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse('agent_settings:test'))
        assert response.context['test_messages'] == []


# ---------------------------------------------------------------------------
# Fotos desde el chat de prueba
# ---------------------------------------------------------------------------

def _jpeg_with_gps():
    from io import BytesIO

    from PIL import Image

    exif = Image.Exif()
    exif.get_ifd(0x8825)[1] = 'N'
    buffer = BytesIO()
    Image.new('RGB', (40, 20), (200, 60, 60)).save(buffer, format='JPEG', exif=exif)
    return buffer.getvalue()


def _send_photo(client, content=None, message=''):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile('herida.jpg', content or _jpeg_with_gps(), content_type='image/jpeg')
    return client.post(reverse('agent_settings:test-send'), {'file': upload, 'message': message})


@contextmanager
def _n8n_captures(payload='{"reply": "La clínica la revisará."}'):
    """Como `_n8n_replies`, pero guarda lo que Django manda a n8n."""
    sent = []

    class _Response:
        def read(self):
            return payload.encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Response()

    with patch('urllib.request.urlopen', side_effect=_urlopen):
        yield sent


@pytest.mark.django_db
class TestAgentTestPhoto:
    def test_photo_is_recorded_as_an_image_message_with_attachment(self, client, admin_user):
        client.force_login(admin_user)

        with _n8n_captures():
            response = _send_photo(client, message='Me duele aquí')

        assert response.status_code == 200
        inbound = ChatMessage.objects.get(direction=ChatMessage.Direction.INBOUND)
        assert inbound.message_type == ChatMessage.MessageType.IMAGE
        assert inbound.body == 'Me duele aquí'
        assert inbound.media is not None
        assert inbound.media.mime_type == 'image/jpeg'

    def test_n8n_is_told_about_the_photo_but_never_gets_it(self, client, admin_user):
        client.force_login(admin_user)

        with _n8n_captures() as sent:
            _send_photo(client, message='Me duele aquí')

        assert sent == [{
            'clinic_id': admin_user.clinic.clinic_id,
            'phone': sent[0]['phone'],
            'message': 'Me duele aquí',
            'message_type': 'image',
        }]

    def test_text_messages_still_go_as_text(self, client, admin_user):
        client.force_login(admin_user)

        with _n8n_captures() as sent:
            _send(client)

        assert sent[0]['message_type'] == 'text'

    def test_photo_without_caption_uses_the_marker(self, client, admin_user):
        client.force_login(admin_user)

        with _n8n_captures():
            _send_photo(client)

        inbound = ChatMessage.objects.get(direction=ChatMessage.Direction.INBOUND)
        assert inbound.body == '[image]'
        assert inbound.display_body == ''

    def test_invalid_file_is_rejected_and_leaves_nothing(self, client, admin_user):
        client.force_login(admin_user)

        with _n8n_captures() as sent:
            response = _send_photo(client, content=b'esto no es una imagen')

        assert response.status_code == 400
        assert 'error' in response.json()
        assert not ChatMessage.objects.exists()
        assert sent == []

    def test_history_links_the_photo_through_the_protected_view(self, client, admin_user):
        client.force_login(admin_user)
        with _n8n_captures():
            _send_photo(client)

        history = client.get(reverse('agent_settings:test')).context['test_messages']

        inbound = ChatMessage.objects.get(direction=ChatMessage.Direction.INBOUND)
        assert history[0]['image_url'] == reverse('agent:chat-media', args=[inbound.pk])
        assert history[0]['text'] == ''
        assert history[1]['image_url'] == ''
