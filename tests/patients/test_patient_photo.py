"""Foto de perfil del paciente, desde el formulario de edición.

1. Se guarda en el almacén clínico privado, con clave UUID, recortada a un
   cuadrado y **sin metadatos**; lo que no es una imagen no entra.
2. Se reemplaza y se quita, y el objeto antiguo se borra del bucket solo cuando
   el guardado se confirma.
3. Se sirve solo por `patients:photo`: aislada por clínica, con `AccessLog` y
   `no-store`. La API (la puerta del agente) no la expone.
"""
from io import BytesIO

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from audit.models import AccessLog
from patients.photos import PHOTO_SIZE


def _exif_with_gps():
    exif = Image.Exif()
    exif[0x010F] = 'PhoneMaker'
    exif[0x0112] = 6
    gps = exif.get_ifd(0x8825)
    gps[1] = 'N'
    gps[2] = (40.0, 25.0, 0.0)
    return exif


def upload(size=(800, 400), image_format='JPEG', exif=True, name='cara.jpg'):
    buffer = BytesIO()
    kwargs = {'exif': _exif_with_gps()} if exif and image_format == 'JPEG' else {}
    Image.new('RGB', size, (200, 60, 60)).save(buffer, format=image_format, **kwargs)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


def payload(patient, **extra):
    data = {
        'first_name': patient.first_name,
        'last_name': patient.last_name,
        'email': patient.email,
        'phone': patient.phone,
        'date_of_birth': '',
        'notes': '',
    }
    data.update(extra)
    return data


def edit_url(patient):
    return reverse('patients:edit', kwargs={'id': patient.pk})


def photo_url(patient):
    return reverse('patients:photo', kwargs={'id': patient.pk})


def stored_image(patient):
    with storages['clinical_media'].open(patient.photo.name) as handle:
        image = Image.open(BytesIO(handle.read()))
        image.load()
    return image


@pytest.fixture
def web_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
class TestUpload:
    def test_stores_square_jpeg_without_metadata_in_private_bucket(self, web_client, patient_a):
        response = web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))

        assert response.status_code == 302
        patient_a.refresh_from_db()
        assert patient_a.photo.name.startswith('patient-photos/')
        assert 'cara' not in patient_a.photo.name
        image = stored_image(patient_a)
        assert image.format == 'JPEG'
        assert image.size == (PHOTO_SIZE, PHOTO_SIZE)
        assert not image.getexif()

    def test_edit_without_photo_keeps_current_one(self, web_client, patient_a):
        web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        patient_a.refresh_from_db()
        name = patient_a.photo.name

        web_client.post(edit_url(patient_a), payload(patient_a, first_name='Juan'))

        patient_a.refresh_from_db()
        assert patient_a.first_name == 'Juan'
        assert patient_a.photo.name == name

    def test_rejects_non_image_and_saves_nothing(self, web_client, patient_a):
        fake = SimpleUploadedFile('cara.jpg', b'%PDF-1.4 no soy una foto', content_type='image/jpeg')

        response = web_client.post(
            edit_url(patient_a), payload(patient_a, first_name='Otro', photo=fake)
        )

        assert response.status_code == 200
        assert response.context['form'].errors['photo']
        patient_a.refresh_from_db()
        assert patient_a.first_name == 'John'
        assert not patient_a.photo

    def test_replacing_deletes_old_object_on_commit(
        self, web_client, patient_a, django_capture_on_commit_callbacks
    ):
        web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        patient_a.refresh_from_db()
        old = patient_a.photo.name

        with django_capture_on_commit_callbacks(execute=True):
            web_client.post(edit_url(patient_a), payload(patient_a, photo=upload(size=(300, 300))))

        patient_a.refresh_from_db()
        assert patient_a.photo.name != old
        assert not storages['clinical_media'].exists(old)
        assert storages['clinical_media'].exists(patient_a.photo.name)

    def test_remove_photo(self, web_client, patient_a, django_capture_on_commit_callbacks):
        web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        patient_a.refresh_from_db()
        old = patient_a.photo.name

        with django_capture_on_commit_callbacks(execute=True):
            web_client.post(edit_url(patient_a), payload(patient_a, remove_photo='on'))

        patient_a.refresh_from_db()
        assert not patient_a.photo
        assert not storages['clinical_media'].exists(old)

    def test_other_clinic_cannot_edit(self, client, admin_user_b, patient_a):
        client.force_login(admin_user_b)
        response = client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        assert response.status_code == 404


@pytest.mark.django_db
class TestServing:
    @pytest.fixture
    def patient_with_photo(self, web_client, patient_a):
        web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        patient_a.refresh_from_db()
        return patient_a

    def test_redirects_logs_access_and_allows_only_private_cache(self, web_client, patient_with_photo):
        before = AccessLog.objects.filter(action=AccessLog.Action.DOWNLOAD_ATTACHMENT).count()

        response = web_client.get(photo_url(patient_with_photo))

        assert response.status_code == 302
        assert patient_with_photo.photo.name in response['Location']
        assert response['Cache-Control'] == 'private, max-age=240'
        assert response['Referrer-Policy'] == 'no-referrer'
        logs = AccessLog.objects.filter(action=AccessLog.Action.DOWNLOAD_ATTACHMENT)
        assert logs.count() == before + 1
        assert logs.latest('id').patient_id == patient_with_photo.pk

    def test_other_clinic_gets_404(self, client, admin_user_b, patient_with_photo):
        client.force_login(admin_user_b)
        assert client.get(photo_url(patient_with_photo)).status_code == 404

    def test_anonymous_is_denied(self, client, patient_with_photo):
        # `web_client` (usado para subir la foto) es este mismo `client`.
        client.logout()
        assert client.get(photo_url(patient_with_photo)).status_code == 403

    def test_patient_without_photo_is_404(self, web_client, patient_a):
        assert web_client.get(photo_url(patient_a)).status_code == 404

    def test_api_does_not_expose_photo(self, admin_client, patient_with_photo):
        response = admin_client.get(f'/api/patients/{patient_with_photo.pk}/')
        assert response.status_code == 200
        assert 'photo' not in response.data

    def test_edit_page_renders_photo_through_protected_view(self, web_client, patient_with_photo):
        response = web_client.get(edit_url(patient_with_photo))

        assert response.status_code == 200
        html = response.content.decode()
        assert photo_url(patient_with_photo) in html
        assert patient_with_photo.photo.name not in html


@pytest.mark.django_db
class TestAvatarAcrossPanel:
    """La foto sale en el directorio, la ficha y los chats, siempre por la vista
    protegida y con la versión en la URL (para no servir una foto vieja en caché)."""

    @pytest.fixture
    def patient_with_photo(self, web_client, patient_a):
        web_client.post(edit_url(patient_a), payload(patient_a, photo=upload()))
        patient_a.refresh_from_db()
        return patient_a

    def avatar_src(self, patient):
        return f"{photo_url(patient)}?v={int(patient.updated_at.timestamp())}"

    def assert_avatar(self, response, patient):
        assert response.status_code == 200
        html = response.content.decode()
        assert self.avatar_src(patient) in html
        assert patient.photo.name not in html

    def test_patient_list(self, web_client, patient_with_photo):
        self.assert_avatar(web_client.get(reverse('patients:list')), patient_with_photo)

    def test_patient_detail(self, web_client, patient_with_photo):
        response = web_client.get(reverse('patients:detail', kwargs={'id': patient_with_photo.pk}))
        self.assert_avatar(response, patient_with_photo)

    def test_chat_list_and_open_thread(self, web_client, clinic_a, patient_with_photo):
        from agent.models import ConversationSession

        session = ConversationSession.objects.create(
            clinic=clinic_a, phone=patient_with_photo.phone, patient=patient_with_photo
        )
        self.assert_avatar(web_client.get(reverse('agent:chat-list-fragment')), patient_with_photo)
        response = web_client.get(reverse('agent:chat-thread', kwargs={'session_id': session.id}))
        # Lista + cabecera del hilo abierto.
        assert response.content.decode().count(self.avatar_src(patient_with_photo)) >= 2

    def test_without_photo_shows_initials_only(self, web_client, patient_a):
        response = web_client.get(reverse('patients:list'))
        assert photo_url(patient_a) not in response.content.decode()
