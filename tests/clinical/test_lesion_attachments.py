"""Fotos clínicas: bucket privado, claves opacas y URLs firmadas por permiso.

Cuatro cosas que no pueden romperse sin que un test se entere:

1. La foto **no va al media público** y su clave **no dice nada** del paciente.
2. La URL es **firmada y caduca**; no se guarda en ninguna parte.
3. **Solo la firma quien puede ver la ficha** de ese paciente. El agente, nunca.
4. Lo que no es una imagen admitida —o pesa de más— **no entra**.
"""
import re
import uuid
from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from clinical.attachments import can_view_attachment, can_view_patient, signed_url_for
from clinical.exceptions import ProtectedClinicalRecord
from clinical.files import clinical_media_storage
from clinical.models import LesionAttachment, LesionObservation

from tests.clinical.test_lesions import make_lesion

# Configuración del bucket real (R2). Se usa para comprobar que la URL sale
# firmada; boto3 firma en local, así que no hay ninguna llamada de red.
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


def image_bytes(image_format='JPEG', size=(24, 24), color=(200, 30, 30)):
    """Una imagen de verdad: los tests no valen con bytes inventados."""
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def upload(name='foto_juan_perez_pie_izquierdo.jpg', content=None,
           content_type='image/jpeg'):
    return SimpleUploadedFile(name, content if content is not None else image_bytes(),
                              content_type=content_type)


@pytest.fixture
def lesion_a(db, episode_a):
    return make_lesion(episode_a)


@pytest.fixture
def observation_a(db, lesion_a, visit_a):
    return LesionObservation.objects.create(
        lesion=lesion_a, visit=visit_a, description='Úlcera con bordes limpios'
    )


@pytest.fixture
def attachment_a(db, observation_a):
    return LesionAttachment.objects.create(observation=observation_a, file=upload())


@pytest.mark.django_db
class TestStorageAndKey:
    def test_the_key_is_a_uuid_and_says_nothing_about_the_patient(self, attachment_a):
        name = attachment_a.file.name

        assert 'juan' not in name.lower()
        assert 'perez' not in name.lower()
        assert 'pie' not in name.lower()
        assert re.fullmatch(r'lesion-attachments/[0-9a-f]{2}/[0-9a-f]{32}\.jpg', name), name

    def test_two_attachments_never_share_a_key(self, observation_a):
        first = LesionAttachment.objects.create(observation=observation_a, file=upload())
        second = LesionAttachment.objects.create(observation=observation_a, file=upload())
        assert first.file.name != second.file.name

    def test_it_is_stored_in_the_clinical_backend_not_the_public_one(self, attachment_a):
        from django.core.files.storage import default_storage, storages

        assert storages['clinical_media'].exists(attachment_a.file.name)
        assert not default_storage.exists(attachment_a.file.name)

    def test_no_url_is_persisted(self, attachment_a):
        """Se guarda la clave, nunca un enlace: una URL firmada caduca."""
        stored = LesionAttachment.objects.get(pk=attachment_a.pk)
        assert not stored.file.name.startswith('http')
        assert not any(
            'http' in str(getattr(stored, field.name))
            for field in LesionAttachment._meta.fields
            if isinstance(getattr(stored, field.name), str)
        )

    def test_the_extension_comes_from_the_content_not_the_name(self, observation_a):
        """Un PNG subido como `.jpg` se guarda como PNG."""
        attachment = LesionAttachment.objects.create(
            observation=observation_a,
            file=upload(name='cualquiercosa.jpg', content=image_bytes('PNG')),
        )
        assert attachment.mime_type == 'image/png'
        assert attachment.file.name.endswith('.png')

    def test_the_probe_result_is_what_gets_stored(self, observation_a):
        content = image_bytes()
        attachment = LesionAttachment.objects.create(
            observation=observation_a,
            # El cliente miente en el content-type: da igual, no se usa.
            file=upload(content=content, content_type='application/octet-stream'),
        )
        assert attachment.mime_type == 'image/jpeg'
        assert attachment.size_bytes == len(content)
        assert attachment.checksum.startswith('sha256:')


@pytest.mark.django_db
class TestSignedUrl:
    def test_the_url_is_signed_and_expires(self, attachment_a, admin_user):
        with override_settings(
            STORAGES={**_current_storages(), 'clinical_media': R2_STORAGE}
        ):
            url = signed_url_for(attachment_a, admin_user)

        assert url.startswith('https://')
        assert attachment_a.file.name in url
        # Firma de vida corta: sin estos parámetros, la URL sería permanente.
        assert 'X-Amz-Signature=' in url
        assert 'X-Amz-Credential=' in url
        assert 'X-Amz-Expires=600' in url

    def test_the_bucket_is_configured_private(self):
        """La propiedad que sostiene todo lo demás: el objeto no es público."""
        from config.settings import base

        options = base.STORAGES['clinical_media']['OPTIONS']
        assert base.STORAGES['clinical_media']['BACKEND'] == 'storages.backends.s3.S3Storage'
        assert options['default_acl'] is None
        assert options['querystring_auth'] is True
        assert options['file_overwrite'] is False
        assert options['querystring_expire'] <= 3600

    def test_the_url_is_generated_on_demand_never_stored(self, attachment_a, admin_user):
        first = signed_url_for(attachment_a, admin_user)
        second = signed_url_for(attachment_a, admin_user)
        # Con el backend de test la URL es estable, pero lo que importa es que
        # se pide al backend cada vez y no sale de una columna.
        assert first == second
        assert not LesionAttachment.objects.filter(pk=attachment_a.pk, checksum=first).exists()


def _current_storages():
    from django.conf import settings

    return dict(settings.STORAGES)


@pytest.mark.django_db
class TestWhoCanSeeIt:
    def test_a_user_of_the_same_clinic_can(self, attachment_a, staff_user):
        assert can_view_attachment(staff_user, attachment_a) is True
        assert signed_url_for(attachment_a, staff_user)

    def test_a_user_of_another_clinic_cannot(self, attachment_a, admin_user_b):
        assert can_view_attachment(admin_user_b, attachment_a) is False
        with pytest.raises(PermissionDenied):
            signed_url_for(attachment_a, admin_user_b)

    def test_the_superuser_can(self, attachment_a, superuser):
        assert can_view_attachment(superuser, attachment_a) is True

    def test_the_n8n_agent_never_can(self, attachment_a, clinic_a, patient_a):
        """Ni con la Api-Key de la clínica del propio paciente."""
        from core.authentication import ClinicAgent

        agent = ClinicAgent(clinic_a)
        assert can_view_patient(agent, patient_a) is False
        assert can_view_attachment(agent, attachment_a) is False
        with pytest.raises(PermissionDenied):
            signed_url_for(attachment_a, agent)

    def test_an_anonymous_user_cannot(self, attachment_a):
        from django.contrib.auth.models import AnonymousUser

        assert can_view_attachment(AnonymousUser(), attachment_a) is False

    def test_a_user_without_clinic_cannot(self, attachment_a, db):
        """«Sin clínica» no puede significar «todas» cuando hay dato clínico."""
        from core.models import User

        orphan = User.objects.create_user(email='orphan@nowhere.test', password='x')
        assert can_view_attachment(orphan, attachment_a) is False


@pytest.mark.django_db
class TestProtectedServing:
    def url_for(self, attachment):
        return reverse('clinical:lesion-attachment', args=[attachment.public_id])

    def logged_client(self, user):
        # Un cliente por usuario: compartir instancia deja la sesión del
        # anterior y el test pasaría por el motivo equivocado.
        client = Client()
        client.force_login(user)
        return client

    def test_it_redirects_to_the_signed_url(self, attachment_a, staff_user):
        response = self.logged_client(staff_user).get(self.url_for(attachment_a))

        assert response.status_code == 302
        assert attachment_a.file.name in response['Location']
        assert 'no-store' in response['Cache-Control']

    def test_a_user_of_another_clinic_gets_403(self, attachment_a, admin_user_b):
        response = self.logged_client(admin_user_b).get(self.url_for(attachment_a))
        assert response.status_code == 403

    def test_anonymous_is_sent_to_login_never_to_the_file(self, attachment_a, client):
        response = client.get(self.url_for(attachment_a))

        assert response.status_code == 302
        assert '/login/' in response['Location']
        assert attachment_a.file.name not in response['Location']

    def test_an_unknown_id_is_404(self, staff_user, db):
        response = self.logged_client(staff_user).get(
            reverse('clinical:lesion-attachment', args=[uuid.uuid4()])
        )
        assert response.status_code == 404

    def test_the_download_is_recorded_in_the_access_log(self, attachment_a, staff_user, patient_a):
        from audit.models import AccessLog

        self.logged_client(staff_user).get(self.url_for(attachment_a))

        entry = AccessLog.objects.filter(
            action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
            object_id=str(attachment_a.pk),
        ).latest('timestamp')
        assert entry.patient_id == patient_a.pk
        assert entry.user_id == staff_user.pk

    def test_a_denied_attempt_leaves_no_download_in_the_log(self, attachment_a, admin_user_b):
        from audit.models import AccessLog

        self.logged_client(admin_user_b).get(self.url_for(attachment_a))

        assert not AccessLog.objects.filter(
            action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
            object_id=str(attachment_a.pk),
        ).exists()


@pytest.mark.django_db
class TestValidation:
    def test_a_pdf_disguised_as_a_jpg_is_rejected(self, observation_a):
        """La extensión y el content-type los pone quien sube: no valen."""
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(
                observation=observation_a,
                file=upload(name='radiografia.jpg', content=b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n'),
            )
        assert LesionAttachment.objects.count() == 0

    def test_an_svg_is_rejected(self, observation_a):
        """Un SVG es un documento con scripts, no una foto."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(
                observation=observation_a, file=upload(name='foto.svg', content=svg,
                                                       content_type='image/svg+xml'),
            )

    def test_a_gif_is_rejected_even_being_a_real_image(self, observation_a):
        """Lista blanca: JPEG, PNG y WebP. Lo demás fuera, aunque sea imagen."""
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(
                observation=observation_a,
                file=upload(name='foto.gif', content=image_bytes('GIF'),
                            content_type='image/gif'),
            )

    def test_an_empty_file_is_rejected(self, observation_a):
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(
                observation=observation_a, file=upload(content=b''),
            )

    def test_an_attachment_without_a_file_is_rejected(self, observation_a):
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(observation=observation_a)

    @pytest.mark.parametrize('image_format', ['JPEG', 'PNG', 'WEBP'])
    def test_the_allowed_formats_go_through(self, observation_a, image_format):
        attachment = LesionAttachment.objects.create(
            observation=observation_a,
            file=upload(name='foto.bin', content=image_bytes(image_format)),
        )
        assert attachment.mime_type in ('image/jpeg', 'image/png', 'image/webp')

    @override_settings(CLINICAL_ATTACHMENT_MAX_BYTES=512)
    def test_an_oversized_file_is_rejected(self, observation_a):
        big = image_bytes(size=(600, 600), color=(12, 200, 90))
        assert len(big) > 512

        with pytest.raises(ValidationError) as exc:
            LesionAttachment.objects.create(observation=observation_a, file=upload(content=big))
        assert 'máximo' in str(exc.value)

    @override_settings(
        CLINICAL_ATTACHMENT_MAX_BYTES=1024 * 1024,
        CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL=512,
    )
    def test_what_comes_from_outside_the_clinic_has_a_tighter_limit(self, observation_a):
        content = image_bytes(size=(600, 600), color=(12, 200, 90))
        assert 512 < len(content) < 1024 * 1024

        # Del profesional pasa...
        LesionAttachment.objects.create(
            observation=observation_a, file=upload(content=content),
            source=LesionAttachment.Source.PROFESSIONAL,
        )
        # ...y exactamente el mismo fichero por WhatsApp, no.
        with pytest.raises(ValidationError):
            LesionAttachment.objects.create(
                observation=observation_a, file=upload(content=content),
                source=LesionAttachment.Source.PATIENT_WHATSAPP,
            )

    def test_full_clean_reports_it_on_the_file_field(self, observation_a):
        attachment = LesionAttachment(
            observation=observation_a, file=upload(content=b'no soy una imagen'),
        )
        with pytest.raises(ValidationError) as exc:
            attachment.clean()
        assert 'file' in exc.value.message_dict


@pytest.mark.django_db
class TestTheAttachmentIsFrozen:
    def test_the_file_cannot_be_replaced(self, attachment_a):
        fresh = LesionAttachment.objects.get(pk=attachment_a.pk)
        fresh.file = upload(content=image_bytes('PNG'))
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_source_cannot_be_rewritten(self, attachment_a):
        fresh = LesionAttachment.objects.get(pk=attachment_a.pk)
        fresh.source = LesionAttachment.Source.PATIENT_WHATSAPP
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_it_cannot_be_moved_to_another_observation(self, attachment_a, lesion_a, visit_a):
        other = LesionObservation.objects.create(lesion=lesion_a, visit=visit_a)

        fresh = LesionAttachment.objects.get(pk=attachment_a.pk)
        fresh.observation = other
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_correcting_means_deleting_and_uploading_another(self, attachment_a, observation_a):
        attachment_a.delete()
        replacement = LesionAttachment.objects.create(
            observation=observation_a, file=upload(content=image_bytes('PNG'))
        )

        assert not LesionAttachment.objects.filter(pk=attachment_a.pk).exists()
        assert LesionAttachment.all_objects.get(pk=attachment_a.pk).deleted_at is not None
        assert replacement.pk != attachment_a.pk

    def test_a_soft_deleted_attachment_keeps_its_object_in_the_bucket(self, attachment_a):
        """El borrado es lógico: el fichero sigue mientras corra la conservación."""
        from django.core.files.storage import storages

        name = attachment_a.file.name
        attachment_a.delete()

        assert storages['clinical_media'].exists(name)

    def test_deleting_the_observation_cascades_to_its_attachments(self, observation_a, attachment_a):
        observation_a.delete()

        assert not LesionAttachment.objects.filter(pk=attachment_a.pk).exists()
        assert LesionAttachment.all_objects.get(pk=attachment_a.pk).deleted_at is not None


@pytest.mark.django_db
class TestAudit:
    def test_the_upload_is_recorded_without_leaking_the_key(self, observation_a, patient_a):
        from audit.models import ChangeLog

        attachment = LesionAttachment.objects.create(
            observation=observation_a, file=upload()
        )

        entry = ChangeLog.objects.filter(
            model_label='clinical.LesionAttachment',
            object_id=str(attachment.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert entry.patient_id == patient_a.pk
        # El canal y el tipo, enteros; la clave del objeto, enmascarada: el log
        # no puede ser el índice del bucket.
        assert entry.changes['source']['after'] == 'professional'
        assert attachment.file.name not in str(entry.changes)


@pytest.mark.django_db
class TestAdmin:
    def test_attachment_pages_are_reachable(self, admin_site_client, attachment_a):
        for url in (
            '/admin/clinical/lesionattachment/',
            f'/admin/clinical/lesionattachment/{attachment_a.pk}/change/',
            '/admin/clinical/lesionattachment/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_the_admin_links_to_the_protected_view_not_to_the_bucket(
        self, admin_site_client, attachment_a
    ):
        response = admin_site_client.get(
            f'/admin/clinical/lesionattachment/{attachment_a.pk}/change/'
        )
        body = response.content.decode()
        assert reverse('clinical:lesion-attachment', args=[attachment_a.public_id]) in body


@pytest.mark.django_db
class TestStorageProxy:
    def test_the_backend_follows_the_settings(self):
        """El campo no puede quedar atado al backend del arranque."""
        storage = clinical_media_storage()
        with override_settings(
            STORAGES={**_current_storages(), 'clinical_media': R2_STORAGE}
        ):
            assert storage.bucket_name == 'clinical-test'
