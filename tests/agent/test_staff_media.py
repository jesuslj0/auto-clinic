"""Imágenes y audios que el staff manda desde el panel."""
import json
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from agent import whatsapp
from agent.models import ChatAttachment, ChatMessage, ConversationSession
from agent.services import record_message, send_staff_media
from agent.whatsapp import WhatsAppError

OGG_OPUS = b'OggS' + b'\x00' * 24 + b'\x01\x13' + b'OpusHead' + b'\x01\x01' + b'\x00' * 200
MP3_ID3 = b'ID3\x04\x00\x00' + b'\x00' * 200


def _image(fmt='JPEG'):
    buffer = BytesIO()
    Image.new('RGB', (30, 20), (200, 60, 60)).save(buffer, format=fmt)
    return buffer.getvalue()


def _file(content, name='archivo'):
    return SimpleUploadedFile(name, content)


@pytest.fixture
def active_session(db, clinic_a, patient_a):
    session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone)
    record_message(
        clinic=clinic_a, session=session,
        direction=ChatMessage.Direction.INBOUND, sender=ChatMessage.Sender.PATIENT,
        body='Hola',
    )
    session.refresh_from_db()
    return session


@pytest.fixture
def meta():
    with patch('agent.services.upload_media', return_value='media.1') as upload, \
            patch('agent.services.send_media', return_value='wamid.MEDIA') as send:
        yield upload, send


@pytest.mark.django_db
class TestSendStaffMedia:
    def test_image_with_caption(self, active_session, meta):
        upload, send = meta
        message = send_staff_media(session=active_session, file=_file(_image()), caption='Así queda')

        assert message.message_type == ChatMessage.MessageType.IMAGE
        assert message.sender == ChatMessage.Sender.STAFF
        assert message.direction == ChatMessage.Direction.OUTBOUND
        assert message.status == ChatMessage.Status.SENT
        assert message.wa_message_id == 'wamid.MEDIA'
        assert message.body == 'Así queda'
        assert message.media.kind == ChatAttachment.Kind.IMAGE
        # A Meta se le sube lo guardado, no el original.
        assert upload.call_args.args[1] == message.media.file.open('rb').read()
        assert send.call_args.args[2:] == ('image', 'media.1', 'Así queda')

        active_session.refresh_from_db()
        assert active_session.last_staff_message_at is not None
        assert active_session.last_message_preview == 'Así queda'

    @pytest.mark.parametrize('content', [OGG_OPUS, MP3_ID3])
    def test_audio(self, active_session, meta, content):
        message = send_staff_media(session=active_session, file=_file(content))
        assert message.message_type == ChatMessage.MessageType.AUDIO
        assert message.media.kind == ChatAttachment.Kind.AUDIO
        assert message.display_body == ''
        active_session.refresh_from_db()
        assert active_session.last_message_preview == '🎤 Audio'

    def test_audio_with_text_is_rejected(self, active_session, meta):
        with pytest.raises(ValueError):
            send_staff_media(session=active_session, file=_file(OGG_OPUS), caption='hola')
        assert not ChatMessage.objects.filter(sender='staff').exists()

    def test_invalid_file_leaves_nothing(self, active_session, meta):
        from django.core.exceptions import ValidationError
        with pytest.raises(ValidationError):
            send_staff_media(session=active_session, file=_file(b'<html>no soy una imagen</html>'))
        assert not ChatMessage.objects.filter(sender='staff').exists()
        assert ChatAttachment.objects.count() == 0
        meta[0].assert_not_called()

    def test_webp_is_rejected(self, active_session, meta):
        with pytest.raises(ValueError):
            send_staff_media(session=active_session, file=_file(_image('WEBP')))
        assert not ChatMessage.objects.filter(sender='staff').exists()

    def test_closed_window(self, db, clinic_a, meta):
        session = ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')
        with pytest.raises(WhatsAppError):
            send_staff_media(session=session, file=_file(_image()))
        meta[0].assert_not_called()

    def test_meta_failure_is_recorded(self, active_session):
        with patch('agent.services.upload_media', side_effect=WhatsAppError('Token caducado')):
            with pytest.raises(WhatsAppError):
                send_staff_media(session=active_session, file=_file(_image()))
        message = ChatMessage.objects.get(sender='staff')
        assert message.status == ChatMessage.Status.FAILED
        assert message.error_message == 'Token caducado'

    def test_agent_memory_is_not_touched(self, active_session, meta):
        from agent.models import AgentMemory
        before = AgentMemory.objects.count()
        send_staff_media(session=active_session, file=_file(_image()))
        assert AgentMemory.objects.count() == before


@pytest.mark.django_db
class TestSendMediaView:
    def test_posts_file(self, client, staff_user, active_session, meta):
        client.force_login(staff_user)
        response = client.post(
            reverse('agent:chat-send', args=[active_session.id]),
            {'file': _file(_image(), 'foto.jpg'), 'body': ''},
        )
        assert response.status_code == 302
        assert ChatMessage.objects.get(sender='staff').message_type == 'image'

    def test_invalid_file_shows_error(self, client, staff_user, active_session, meta):
        client.force_login(staff_user)
        response = client.post(
            reverse('agent:chat-send', args=[active_session.id]),
            {'file': _file(b'nope', 'x.jpg')}, follow=True,
        )
        assert response.status_code == 200
        assert not ChatMessage.objects.filter(sender='staff').exists()
        assert list(response.context['messages'])

    def test_other_clinic_is_404(self, client, staff_user, db, clinic_b, meta):
        other = ConversationSession.objects.create(clinic=clinic_b, phone='+34699999999')
        client.force_login(staff_user)
        response = client.post(
            reverse('agent:chat-send', args=[other.id]), {'file': _file(_image())}
        )
        assert response.status_code == 404

    def test_n8n_key_still_cannot_attach_outbound(self, active_session, meta):
        """`attach_media` (la vía del agente) sigue vedada a mensajes salientes."""
        from django.core.exceptions import ValidationError
        from agent.media import attach_media
        message = send_staff_media(session=active_session, file=_file(_image()))
        with pytest.raises(ValidationError):
            attach_media(message, _file(_image()))


class TestWhatsAppRequests:
    def test_upload_media_builds_multipart(self):
        class Clinic:
            whatsapp_phone_number_id = '123'
            whatsapp_token = 'tok'

        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({'id': 'MEDIA42'}).encode()

        def fake_urlopen(request, timeout):
            captured['url'] = request.full_url
            captured['body'] = request.data
            captured['ct'] = request.get_header('Content-type')
            return Response()

        with patch('agent.whatsapp.urllib.request.urlopen', fake_urlopen):
            media_id = whatsapp.upload_media(Clinic, b'BINARY', 'image/jpeg', 'image.jpg')

        assert media_id == 'MEDIA42'
        assert captured['url'].endswith('/123/media')
        assert captured['ct'].startswith('multipart/form-data; boundary=')
        assert b'BINARY' in captured['body']
        assert b'name="messaging_product"' in captured['body']
