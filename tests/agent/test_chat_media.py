"""Fotos y notas de voz de WhatsApp: entrada, limpieza, custodia y servido."""
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import override_settings
from django.urls import reverse
from PIL import Image, PngImagePlugin
from rest_framework.test import APIClient

from agent.files import prepare_chat_audio, prepare_chat_image, strip_image_metadata
from agent.media import MediaAlreadyAttached, attach_media, can_view_chat_media, signed_media_url
from agent.models import ChatAttachment, ChatAttachmentImmutable, ChatMessage, ConversationSession
from agent.services import build_preview, record_message
from audit.models import AccessLog, ChangeLog
from core.authentication import ClinicAgent

R2_STORAGE = {
    'BACKEND': 'storages.backends.s3.S3Storage',
    'OPTIONS': {
        'access_key': 'test-access-key',
        'secret_key': 'test-secret-key',
        'bucket_name': 'clinical-test',
        'endpoint_url': 'https://accountid.r2.cloudflarestorage.com',
        'region_name': 'auto',
        'signature_version': 's3v4',
        'addressing_style': 'virtual',
        'default_acl': None,
        'querystring_auth': True,
        'querystring_expire': 600,
        'file_overwrite': False,
    },
}


# ---------------------------------------------------------------------------
# Ficheros de prueba (de verdad: los validadores decodifican)
# ---------------------------------------------------------------------------

def _exif_with_gps():
    exif = Image.Exif()
    exif[0x010F] = 'PhoneMaker'   # Make
    exif[0x0110] = 'PhoneModel'   # Model
    exif[0x0112] = 6              # Orientation: girar 90°
    gps = exif.get_ifd(0x8825)
    gps[1] = 'N'
    gps[2] = (40.0, 25.0, 0.0)
    return exif


def jpeg_with_exif(size=(40, 20)):
    buffer = BytesIO()
    Image.new('RGB', size, (200, 60, 60)).save(buffer, format='JPEG', exif=_exif_with_gps())
    return buffer.getvalue()


def png_with_text():
    info = PngImagePlugin.PngInfo()
    info.add_text('Comment', 'Juan Perez, pie izquierdo')
    buffer = BytesIO()
    Image.new('RGBA', (30, 30), (0, 0, 255, 128)).save(buffer, format='PNG', pnginfo=info)
    return buffer.getvalue()


def webp_with_exif():
    buffer = BytesIO()
    Image.new('RGB', (30, 30), (0, 200, 0)).save(buffer, format='WEBP', exif=_exif_with_gps())
    return buffer.getvalue()


def animated_webp():
    frames = [Image.new('RGB', (16, 16), color) for color in ((255, 0, 0), (0, 0, 255))]
    buffer = BytesIO()
    frames[0].save(buffer, format='WEBP', save_all=True, append_images=frames[1:], duration=100)
    return buffer.getvalue()


def palette_png_with_transparency():
    image = Image.new('P', (10, 10), 0)
    image.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    buffer = BytesIO()
    image.save(buffer, format='PNG', transparency=0)
    return buffer.getvalue()


OGG_OPUS = b'OggS' + b'\x00' * 24 + b'\x01\x13' + b'OpusHead' + b'\x01\x01' + b'\x00' * 200
OGG_THEORA = b'OggS' + b'\x00' * 24 + b'\x01\x2a' + b'\x80theora' + b'\x00' * 200
MP3_ID3 = b'ID3\x04\x00\x00' + b'\x00' * 200
MP3_FRAME = b'\xff\xfb\x90\x00' + b'\x00' * 200


def upload(content, name='foto_juan_perez.jpg', content_type='image/jpeg'):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _open(content):
    return Image.open(BytesIO(content))


# ---------------------------------------------------------------------------
# Limpieza de metadatos
# ---------------------------------------------------------------------------

class TestMetadataStripping:
    def test_jpeg_loses_exif_and_gps(self):
        prepared = prepare_chat_image(upload(jpeg_with_exif()))
        image = _open(prepared.content)
        assert len(image.getexif()) == 0
        assert b'PhoneMaker' not in prepared.content

    def test_orientation_is_applied_before_dropping_exif(self):
        # 40x20 con «gira 90°»: se tiene que ver 20x40, no tumbada.
        prepared = prepare_chat_image(upload(jpeg_with_exif(size=(40, 20))))
        assert _open(prepared.content).size == (20, 40)

    def test_png_loses_text_chunks_and_keeps_transparency(self):
        prepared = prepare_chat_image(upload(png_with_text(), name='x.png', content_type='image/png'))
        image = _open(prepared.content)
        assert 'Comment' not in image.info
        assert b'Juan Perez' not in prepared.content
        assert image.mode == 'RGBA'

    def test_palette_png_keeps_its_transparency(self):
        prepared = prepare_chat_image(upload(palette_png_with_transparency(), name='x.png'))
        assert _open(prepared.content).info.get('transparency') == 0

    def test_webp_loses_exif(self):
        prepared = prepare_chat_image(upload(webp_with_exif(), name='x.webp'))
        image = _open(prepared.content)
        assert prepared.mime_type == 'image/webp'
        assert not image.info.get('exif')
        assert b'PhoneMaker' not in prepared.content

    def test_animated_webp_keeps_its_frames(self):
        # Los stickers de WhatsApp llegan como imagen.
        prepared = prepare_chat_image(upload(animated_webp(), name='sticker.webp'))
        assert getattr(_open(prepared.content), 'n_frames', 1) == 2

    def test_size_and_checksum_describe_the_stored_file(self):
        prepared = prepare_chat_image(upload(jpeg_with_exif()))
        assert prepared.size_bytes == len(prepared.content)
        assert prepared.checksum.startswith('sha256:')

    def test_a_pdf_named_jpg_is_rejected(self):
        with pytest.raises(ValidationError):
            prepare_chat_image(upload(b'%PDF-1.7\n' + b'0' * 500))

    def test_strip_is_idempotent_on_clean_images(self):
        clean = prepare_chat_image(upload(jpeg_with_exif())).content
        again = strip_image_metadata(BytesIO(clean), 'image/jpeg')
        assert len(_open(again).getexif()) == 0


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

class TestAudioValidation:
    @pytest.mark.parametrize('content, mime', [
        (OGG_OPUS, 'audio/ogg'), (MP3_ID3, 'audio/mpeg'), (MP3_FRAME, 'audio/mpeg'),
    ])
    def test_accepts_voice_notes_and_mp3(self, content, mime):
        assert prepare_chat_audio(upload(content, name='a.bin')).mime_type == mime

    @pytest.mark.parametrize('content', [OGG_THEORA, b'RIFF' + b'\x00' * 200, jpeg_with_exif()])
    def test_rejects_anything_else(self, content):
        with pytest.raises(ValidationError):
            prepare_chat_audio(upload(content, name='nota.ogg'))

    def test_rejects_oversized_audio(self):
        with override_settings(CHAT_AUDIO_MAX_BYTES=100):
            with pytest.raises(ValidationError):
                prepare_chat_audio(upload(OGG_OPUS, name='nota.ogg'))


# ---------------------------------------------------------------------------
# Custodia: irremplazable
# ---------------------------------------------------------------------------

@pytest.fixture
def session_a(db, clinic_a):
    # Sin ficha de paciente: alguien que escribe por primera vez.
    return ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')


def _inbound(session, message_type='image', body='[image]'):
    return record_message(
        clinic=session.clinic, session=session, direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT, body=body, message_type=message_type,
    )


@pytest.fixture
def image_message(session_a):
    return _inbound(session_a)


@pytest.fixture
def attachment(image_message):
    return attach_media(image_message, upload(jpeg_with_exif()))


@pytest.mark.django_db
class TestAttachmentCustody:
    def test_key_is_a_uuid_under_its_own_prefix(self, attachment):
        assert attachment.file.name.startswith('chat-media/')
        assert 'juan' not in attachment.file.name
        assert attachment.file.name.endswith('.jpg')

    def test_stored_file_has_no_metadata(self, attachment):
        attachment.file.open('rb')
        stored = attachment.file.read()
        assert len(_open(stored).getexif()) == 0
        assert attachment.size_bytes == len(stored)

    def test_second_upload_is_refused(self, image_message, attachment):
        with pytest.raises(MediaAlreadyAttached):
            attach_media(image_message, upload(jpeg_with_exif()))
        assert ChatAttachment.objects.filter(message=image_message).count() == 1

    def test_orm_cannot_modify_it(self, attachment):
        attachment.mime_type = 'image/png'
        with pytest.raises(ChatAttachmentImmutable):
            attachment.save()

    def test_orm_cannot_delete_it(self, attachment):
        with pytest.raises(ChatAttachmentImmutable):
            attachment.delete()

    def test_database_refuses_updates(self, attachment):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                ChatAttachment.objects.filter(pk=attachment.pk).update(mime_type='image/png')

    def test_deleting_the_conversation_still_works(self, session_a, attachment):
        session_a.delete()
        assert not ChatAttachment.objects.filter(pk=attachment.pk).exists()

    def test_text_message_does_not_take_attachments(self, session_a):
        message = _inbound(session_a, message_type='text', body='hola')
        with pytest.raises(ValidationError):
            attach_media(message, upload(jpeg_with_exif()))

    def test_outbound_message_does_not_take_attachments(self, session_a):
        message = record_message(
            clinic=session_a.clinic, session=session_a, direction=ChatMessage.Direction.OUTBOUND,
            sender=ChatMessage.Sender.AGENT, body='[image]', message_type='image',
        )
        with pytest.raises(ValidationError):
            attach_media(message, upload(jpeg_with_exif()))

    def test_is_audited_without_a_patient(self, attachment):
        entry = ChangeLog.objects.filter(object_id=str(attachment.pk)).get()
        assert entry.action == 'create'

    def test_message_body_is_masked_in_the_audit(self, session_a):
        message = _inbound(session_a, message_type='text', body='Me sangra el corte')
        entry = ChangeLog.objects.filter(object_id=str(message.pk)).get()
        assert 'Me sangra' not in str(entry.changes)

    def test_attaching_emits_the_message_again(self, image_message, django_capture_on_commit_callbacks):
        with patch('agent.realtime.send_to_group') as send:
            with django_capture_on_commit_callbacks(execute=True):
                attach_media(image_message, upload(jpeg_with_exif()))
        events = [c.args[1] for c in send.call_args_list]
        assert any(e['type'] == 'chat.message' and e['payload']['message_id'] == str(image_message.pk) for e in events)


# ---------------------------------------------------------------------------
# Subida desde n8n
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_client(clinic_a):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return client


def _upload_url(message):
    return f'/api/agent/messages/{message.pk}/media/'


@pytest.mark.django_db
class TestUploadEndpoint:
    def test_agent_uploads_and_gets_no_url_back(self, agent_client, image_message):
        response = agent_client.post(_upload_url(image_message), {'file': upload(jpeg_with_exif())}, format='multipart')
        assert response.status_code == 201
        assert response.data['mime_type'] == 'image/jpeg'
        assert 'url' not in str(response.data).lower()
        assert image_message.media is not None or ChatAttachment.objects.filter(message=image_message).exists()

    def test_second_upload_is_409(self, agent_client, image_message, attachment):
        response = agent_client.post(_upload_url(image_message), {'file': upload(jpeg_with_exif())}, format='multipart')
        assert response.status_code == 409

    def test_fake_image_is_400(self, agent_client, image_message):
        response = agent_client.post(
            _upload_url(image_message), {'file': upload(b'<svg onload=alert(1)>', name='x.jpg')}, format='multipart'
        )
        assert response.status_code == 400
        assert not ChatAttachment.objects.exists()

    def test_missing_file_is_400(self, agent_client, image_message):
        assert agent_client.post(_upload_url(image_message), {}, format='multipart').status_code == 400

    def test_voice_note(self, agent_client, session_a):
        message = _inbound(session_a, message_type='audio', body='[audio]')
        response = agent_client.post(
            _upload_url(message), {'file': upload(OGG_OPUS, name='nota.ogg', content_type='audio/ogg')}, format='multipart'
        )
        assert response.status_code == 201
        assert response.data['kind'] == 'audio'

    def test_other_clinic_message_is_404(self, clinic_b, image_message):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_b.agent_api_key}')
        response = client.post(_upload_url(image_message), {'file': upload(jpeg_with_exif())}, format='multipart')
        assert response.status_code == 404

    def test_staff_cannot_use_it(self, staff_user, image_message):
        client = APIClient()
        client.force_authenticate(staff_user)
        response = client.post(_upload_url(image_message), {'file': upload(jpeg_with_exif())}, format='multipart')
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Servido al panel
# ---------------------------------------------------------------------------

def _media_url(message):
    return reverse('agent:chat-media', args=[message.pk])


@pytest.mark.django_db
class TestServing:
    def test_staff_of_the_clinic_sees_photos_of_people_without_a_file(self, client, staff_user, image_message, attachment):
        # Sin ficha de paciente (aún sin onboarding) la foto se ve igual.
        assert image_message.session.patient is None
        client.force_login(staff_user)
        response = client.get(_media_url(image_message))
        assert response.status_code == 302
        assert attachment.file.name in response['Location']

    def test_every_view_leaves_an_access_log(self, client, staff_user, image_message, attachment):
        client.force_login(staff_user)
        client.get(_media_url(image_message))
        client.get(_media_url(image_message))
        logs = AccessLog.objects.filter(object_id=str(attachment.pk), action=AccessLog.Action.DOWNLOAD_ATTACHMENT)
        assert logs.count() == 2
        assert logs.first().user == staff_user

    def test_redirect_is_not_cached_nor_leaks_referrer(self, client, staff_user, image_message, attachment):
        client.force_login(staff_user)
        response = client.get(_media_url(image_message))
        assert 'no-store' in response['Cache-Control']
        assert response['Referrer-Policy'] == 'no-referrer'

    def test_other_clinic_gets_403_and_no_log(self, client, admin_user_b, image_message, attachment):
        client.force_login(admin_user_b)
        assert client.get(_media_url(image_message)).status_code == 403
        assert not AccessLog.objects.filter(object_id=str(attachment.pk)).exists()

    def test_anonymous_gets_403(self, client, image_message, attachment):
        assert client.get(_media_url(image_message)).status_code == 403

    def test_message_without_attachment_is_404(self, client, staff_user, image_message):
        client.force_login(staff_user)
        assert client.get(_media_url(image_message)).status_code == 404

    def test_agent_is_denied_explicitly(self, clinic_a, attachment):
        assert can_view_chat_media(ClinicAgent(clinic_a), attachment) is False

    def test_inactive_user_is_denied(self, staff_user, attachment):
        staff_user.is_active = False
        assert can_view_chat_media(staff_user, attachment) is False

    def test_signed_url_is_short_lived_and_not_cacheable(self, staff_user, attachment):
        from django.conf import settings

        with override_settings(STORAGES={**settings.STORAGES, 'clinical_media': R2_STORAGE}):
            url = signed_media_url(attachment, staff_user)
        assert 'X-Amz-Signature=' in url
        assert 'X-Amz-Expires=300' in url
        assert 'response-cache-control=private%2C%20no-store' in url
        assert 'response-content-type=image%2Fjpeg' in url

    def test_expiry_is_configurable(self, staff_user, attachment):
        from django.conf import settings

        with override_settings(
            CHAT_MEDIA_URL_EXPIRE=180, STORAGES={**settings.STORAGES, 'clinical_media': R2_STORAGE}
        ):
            assert 'X-Amz-Expires=180' in signed_media_url(attachment, staff_user)


# ---------------------------------------------------------------------------
# Cómo se pinta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRendering:
    def test_photo_is_hidden_behind_tap_to_view(self, client, staff_user, image_message, attachment):
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-thread', args=[image_message.session_id])).content.decode()
        assert 'data-media-open' in html
        assert 'Toca para ver' in html
        # La foto no se carga al abrir el hilo: no hay <img> que apunte a ella.
        import re

        assert not re.search(r'(?<![\w-])src="' + re.escape(_media_url(image_message)), html)
        assert not AccessLog.objects.filter(object_id=str(attachment.pk)).exists()

    def test_viewer_knows_who_and_when(self, client, staff_user, image_message, attachment):
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-thread', args=[image_message.session_id])).content.decode()
        assert f'data-who="{image_message.session.phone}"' in html
        assert 'data-when=' in html

    def test_placeholder_body_is_not_shown(self, client, staff_user, image_message):
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-thread', args=[image_message.session_id])).content.decode()
        assert '[image]' not in html
        assert 'sin descargar' in html

    def test_list_preview_uses_the_icon(self, image_message):
        assert build_preview(image_message) == '📷 Imagen'

    def test_audio_is_not_preloaded(self, client, staff_user, session_a):
        message = _inbound(session_a, message_type='audio', body='[audio]')
        attach_media(message, upload(OGG_OPUS, name='nota.ogg'))
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-thread', args=[session_a.pk])).content.decode()
        assert 'preload="none"' in html

    def test_only_param_returns_one_bubble(self, client, staff_user, image_message, attachment):
        client.force_login(staff_user)
        url = reverse('agent:chat-messages-fragment', args=[image_message.session_id])
        html = client.get(url, {'only': str(image_message.pk)}).content.decode()
        assert html.count('data-message-id=') == 1
        assert 'data-media-open' in html

    def test_only_param_from_another_thread_is_400(self, client, staff_user, clinic_a, image_message):
        other = ConversationSession.objects.create(clinic=clinic_a, phone='+34600999888')
        client.force_login(staff_user)
        url = reverse('agent:chat-messages-fragment', args=[other.pk])
        assert client.get(url, {'only': str(image_message.pk)}).status_code == 400
