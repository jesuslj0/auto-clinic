"""Consentimiento informado: qué firmó exactamente el paciente.

Cuando un consentimiento se discute —y se discute años después— la pregunta no es
si el paciente firmó, sino **qué texto tenía delante cuando firmó**. Aquí se
defienden las cuatro cosas que responden a eso:

1. Firmar **copia el texto** de la versión dentro del registro firmado.
2. Cambiar la versión después **no toca ni una coma** de lo ya firmado.
3. **Una sola versión vigente** por documento, garantizada por la base de datos.
4. La firma vive en el **bucket privado** y se sirve solo con URL firmada y
   permiso comprobado, igual que una foto clínica.
"""
import re
import uuid
from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from clinical.attachments import can_view_attachment, signed_url_for
from clinical.exceptions import (
    ConsentVersionNotPublished,
    ConsentVersionPublished,
    ProtectedClinicalRecord,
)
from clinical.models import ConsentTemplate, ConsentVersion, SignedConsent
from core.managers import ProtectedRecordError

CONSENT_TEXT = (
    'Se me ha informado de que la cirugía ungueal se realiza con anestesia '
    'local y de sus posibles complicaciones: infección, recidiva y dolor '
    'postoperatorio. Consiento en que se me practique.'
)

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


def signature_bytes(image_format='PNG', size=(120, 40)):
    """Una imagen de verdad: los tests no valen con bytes inventados."""
    buffer = BytesIO()
    Image.new('RGB', size, (255, 255, 255)).save(buffer, format=image_format)
    return buffer.getvalue()


def signature(name='firma_juan_perez.png', content=None, content_type='image/png'):
    return SimpleUploadedFile(
        name, content if content is not None else signature_bytes(), content_type=content_type
    )


def _current_storages():
    from django.conf import settings

    return dict(settings.STORAGES)


@pytest.fixture
def consent_template_a(db, clinic_a):
    return ConsentTemplate.objects.create(
        clinic=clinic_a, name='Consentimiento de cirugía ungueal',
        specialty='cirugía ungueal',
    )


@pytest.fixture
def draft_consent_a(db, consent_template_a):
    return ConsentVersion.objects.create(template=consent_template_a, text=CONSENT_TEXT)


@pytest.fixture
def published_consent_a(db, draft_consent_a):
    draft_consent_a.publish()
    return draft_consent_a


@pytest.fixture
def signed_consent_a(db, published_consent_a, patient_a, episode_a):
    return SignedConsent.sign(
        version=published_consent_a, patient=patient_a, episode=episode_a,
        signature_image=signature(),
    )


@pytest.mark.django_db
class TestSigningCopiesTheText:
    def test_signing_copies_the_version_text(self, signed_consent_a):
        assert signed_consent_a.text_copy == CONSENT_TEXT

    def test_the_copy_survives_a_reload(self, signed_consent_a):
        """No es una property que lea la versión: está en su propia columna."""
        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert stored.text_copy == CONSENT_TEXT

    def test_creating_it_directly_copies_the_text_too(
        self, published_consent_a, patient_a, episode_a
    ):
        """El congelado vive en el `save()`: da igual por dónde entre la firma."""
        consent = SignedConsent.objects.create(
            version=published_consent_a, patient=patient_a, episode=episode_a,
            signature_image=signature(),
        )
        assert consent.text_copy == CONSENT_TEXT

    def test_a_draft_version_cannot_be_signed(self, draft_consent_a, patient_a, episode_a):
        """No se le hace firmar a nadie un texto que aún puede cambiar."""
        with pytest.raises(ConsentVersionNotPublished):
            SignedConsent.sign(
                version=draft_consent_a, patient=patient_a, episode=episode_a,
                signature_image=signature(),
            )
        assert SignedConsent.objects.count() == 0

    def test_signing_without_a_signature_is_rejected(
        self, published_consent_a, patient_a, episode_a
    ):
        with pytest.raises(ValidationError):
            SignedConsent.objects.create(
                version=published_consent_a, patient=patient_a, episode=episode_a,
            )
        assert SignedConsent.objects.count() == 0


@pytest.mark.django_db
class TestTheVersionNeverReachesBackwards:
    def test_publishing_a_new_version_does_not_touch_what_was_signed(
        self, signed_consent_a, consent_template_a
    ):
        """El caso que da sentido a todo el modelo."""
        v2 = consent_template_a.new_draft_version(text='Texto nuevo, condiciones distintas.')
        v2.publish()

        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert stored.text_copy == CONSENT_TEXT
        assert consent_template_a.current_version == v2

    def test_a_published_version_cannot_be_edited(self, published_consent_a):
        fresh = ConsentVersion.objects.get(pk=published_consent_a.pk)
        fresh.text = 'Otro texto'

        with pytest.raises(ConsentVersionPublished):
            fresh.save()

    def test_the_database_rejects_it_too(self, published_consent_a, signed_consent_a):
        """Segundo nivel: el trigger para el `UPDATE` en SQL crudo."""
        from django.db import connection

        with pytest.raises(Exception) as exc, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE clinical_consent_version SET text = %s WHERE id = %s',
                    ['Texto reescrito por la puerta de atrás', published_consent_a.pk],
                )
        assert 'inmutable' in str(exc.value)

        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert stored.text_copy == CONSENT_TEXT

    def test_unpublishing_does_not_touch_what_was_signed(self, signed_consent_a, published_consent_a):
        """Retirar el documento de circulación no borra lo que se firmó."""
        published_consent_a.unpublish()

        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert stored.text_copy == CONSENT_TEXT

    def test_the_copy_stands_on_its_own(self, signed_consent_a, published_consent_a):
        """La FK dice de dónde salió; la prueba es la copia.

        Aunque la versión se retire y el documento se desactive, el registro
        firmado sigue diciendo por sí solo qué aceptó el paciente.
        """
        published_consent_a.unpublish()
        published_consent_a.template.is_active = False
        published_consent_a.template.save()

        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert stored.text_copy == CONSENT_TEXT

    def test_a_published_version_cannot_be_deleted(self, published_consent_a):
        assert published_consent_a.can_be_deleted() is False
        with pytest.raises(ProtectedRecordError):
            published_consent_a.delete()

    def test_a_draft_can_be_deleted(self, draft_consent_a):
        draft_consent_a.delete()
        assert not ConsentVersion.objects.filter(pk=draft_consent_a.pk).exists()


@pytest.mark.django_db
class TestOnlyOneCurrentVersion:
    def test_publishing_a_second_version_demotes_the_first(
        self, published_consent_a, consent_template_a
    ):
        v2 = consent_template_a.new_draft_version(text='Segunda redacción.')
        v2.publish()

        published_consent_a.refresh_from_db()
        assert published_consent_a.is_current is False
        assert v2.is_current is True
        assert consent_template_a.versions.filter(is_current=True).count() == 1

    def test_the_database_refuses_two_current_versions(
        self, published_consent_a, consent_template_a
    ):
        """No se sostiene solo en `make_current()`: hay un índice único parcial."""
        v2 = consent_template_a.new_draft_version(text='Segunda redacción.')
        v2.publish(make_current=False)

        v2.is_current = True
        with pytest.raises(IntegrityError), transaction.atomic():
            v2.save()

    def test_current_version_reads_the_live_one(self, published_consent_a, consent_template_a):
        assert consent_template_a.current_version == published_consent_a

    def test_a_new_draft_starts_from_the_current_text(
        self, published_consent_a, consent_template_a
    ):
        v2 = consent_template_a.new_draft_version()

        assert v2.text == CONSENT_TEXT
        assert v2.number == published_consent_a.number + 1
        assert v2.is_published is False

    def test_an_empty_version_cannot_be_published(self, consent_template_a):
        empty = ConsentVersion.objects.create(template=consent_template_a, text='   ')

        with pytest.raises(ValidationError):
            empty.publish()

    def test_a_draft_cannot_be_made_current(self, draft_consent_a):
        with pytest.raises(ConsentVersionNotPublished):
            draft_consent_a.make_current()


@pytest.mark.django_db
class TestTheSignatureIsStoredPrivately:
    def test_the_key_is_a_uuid_and_says_nothing_about_the_patient(self, signed_consent_a):
        name = signed_consent_a.signature_image.name

        assert 'juan' not in name.lower()
        assert 'perez' not in name.lower()
        assert re.fullmatch(r'consent-signatures/[0-9a-f]{2}/[0-9a-f]{32}\.png', name), name

    def test_it_goes_to_the_clinical_backend_not_the_public_one(self, signed_consent_a):
        from django.core.files.storage import default_storage, storages

        name = signed_consent_a.signature_image.name
        assert storages['clinical_media'].exists(name)
        assert not default_storage.exists(name)

    def test_no_url_is_persisted(self, signed_consent_a):
        """Se guarda la clave, nunca un enlace: una URL firmada caduca."""
        stored = SignedConsent.objects.get(pk=signed_consent_a.pk)
        assert not stored.signature_image.name.startswith('http')

    def test_the_content_is_probed_not_trusted(self, published_consent_a, patient_a, episode_a):
        content = signature_bytes()
        consent = SignedConsent.sign(
            version=published_consent_a, patient=patient_a, episode=episode_a,
            # El cliente miente en el content-type: da igual, no se usa.
            signature_image=signature(content=content, content_type='application/octet-stream'),
        )

        assert consent.mime_type == 'image/png'
        assert consent.size_bytes == len(content)
        assert consent.checksum.startswith('sha256:')

    def test_something_that_is_not_an_image_is_rejected(
        self, published_consent_a, patient_a, episode_a
    ):
        with pytest.raises(ValidationError):
            SignedConsent.sign(
                version=published_consent_a, patient=patient_a, episode=episode_a,
                signature_image=signature(name='firma.png', content=b'%PDF-1.7\n'),
            )
        assert SignedConsent.objects.count() == 0

    def test_full_clean_reports_it_on_the_signature_field(
        self, published_consent_a, patient_a, episode_a
    ):
        consent = SignedConsent(
            version=published_consent_a, patient=patient_a, episode=episode_a,
            signature_image=signature(content=b'no soy una imagen'),
        )
        with pytest.raises(ValidationError) as exc:
            consent.clean()
        assert 'signature_image' in exc.value.message_dict


@pytest.mark.django_db
class TestTheSignatureIsServedProtected:
    def url_for(self, consent):
        return reverse('clinical:consent-signature', args=[consent.public_id])

    def logged_client(self, user):
        # Un cliente por usuario: compartir instancia deja la sesión del
        # anterior y el test pasaría por el motivo equivocado.
        client = Client()
        client.force_login(user)
        return client

    def test_the_url_is_signed_and_expires(self, signed_consent_a, admin_user):
        with override_settings(
            STORAGES={**_current_storages(), 'clinical_media': R2_STORAGE}
        ):
            url = signed_url_for(signed_consent_a, admin_user)

        assert url.startswith('https://')
        assert signed_consent_a.signature_image.name in url
        assert 'X-Amz-Signature=' in url
        assert 'X-Amz-Expires=600' in url

    def test_a_user_of_the_same_clinic_can(self, signed_consent_a, staff_user):
        assert can_view_attachment(staff_user, signed_consent_a) is True

        response = self.logged_client(staff_user).get(self.url_for(signed_consent_a))
        assert response.status_code == 302
        assert 'no-store' in response['Cache-Control']

    def test_a_user_of_another_clinic_cannot(self, signed_consent_a, admin_user_b):
        with pytest.raises(PermissionDenied):
            signed_url_for(signed_consent_a, admin_user_b)

        response = self.logged_client(admin_user_b).get(self.url_for(signed_consent_a))
        assert response.status_code == 403

    def test_the_n8n_agent_never_can(self, signed_consent_a, clinic_a):
        """Ni con la Api-Key de la clínica del propio paciente."""
        from core.authentication import ClinicAgent

        agent = ClinicAgent(clinic_a)
        assert can_view_attachment(agent, signed_consent_a) is False
        with pytest.raises(PermissionDenied):
            signed_url_for(signed_consent_a, agent)

    def test_anonymous_is_sent_to_login_never_to_the_file(self, signed_consent_a, client):
        response = client.get(self.url_for(signed_consent_a))

        assert response.status_code == 302
        assert '/login/' in response['Location']
        assert signed_consent_a.signature_image.name not in response['Location']

    def test_an_unknown_id_is_404(self, staff_user, db):
        response = self.logged_client(staff_user).get(
            reverse('clinical:consent-signature', args=[uuid.uuid4()])
        )
        assert response.status_code == 404

    def test_the_download_is_recorded_in_the_access_log(
        self, signed_consent_a, staff_user, patient_a
    ):
        from audit.models import AccessLog

        self.logged_client(staff_user).get(self.url_for(signed_consent_a))

        entry = AccessLog.objects.filter(
            action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
            object_id=str(signed_consent_a.pk),
        ).latest('timestamp')
        assert entry.patient_id == patient_a.pk
        assert entry.user_id == staff_user.pk

    def test_a_denied_attempt_leaves_no_download_in_the_log(
        self, signed_consent_a, admin_user_b
    ):
        from audit.models import AccessLog

        self.logged_client(admin_user_b).get(self.url_for(signed_consent_a))

        assert not AccessLog.objects.filter(
            action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
            object_id=str(signed_consent_a.pk),
        ).exists()


@pytest.mark.django_db
class TestTheSignedRecordIsFrozen:
    def test_the_text_copy_cannot_be_rewritten(self, signed_consent_a):
        fresh = SignedConsent.objects.get(pk=signed_consent_a.pk)
        fresh.text_copy = 'Yo nunca firmé esto'

        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_signature_cannot_be_replaced(self, signed_consent_a):
        fresh = SignedConsent.objects.get(pk=signed_consent_a.pk)
        fresh.signature_image = signature(content=signature_bytes('JPEG'))

        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_it_cannot_be_reassigned_to_another_version(
        self, signed_consent_a, consent_template_a
    ):
        v2 = consent_template_a.new_draft_version(text='Segunda redacción.')
        v2.publish()

        fresh = SignedConsent.objects.get(pk=signed_consent_a.pk)
        fresh.version = v2
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_database_refuses_to_rewrite_the_copy(self, signed_consent_a):
        """Segundo nivel: ni por SQL crudo."""
        from django.db import connection

        with pytest.raises(Exception) as exc, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE clinical_signed_consent SET text_copy = %s WHERE id = %s',
                    ['Otra cosa', signed_consent_a.pk],
                )
        assert 'inmutable' in str(exc.value)

    def test_the_database_refuses_to_delete_it(self, signed_consent_a):
        from django.db import connection

        with pytest.raises(Exception) as exc, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    'DELETE FROM clinical_signed_consent WHERE id = %s', [signed_consent_a.pk]
                )
        assert 'DELETE no permitido' in str(exc.value)

    def test_soft_deleting_keeps_the_object_in_the_bucket(self, signed_consent_a):
        from django.core.files.storage import storages

        name = signed_consent_a.signature_image.name
        signed_consent_a.delete()

        assert not SignedConsent.objects.filter(pk=signed_consent_a.pk).exists()
        assert SignedConsent.all_objects.get(pk=signed_consent_a.pk).deleted_at is not None
        assert storages['clinical_media'].exists(name)


@pytest.mark.django_db
class TestMultiTenancyAndCoherence:
    def test_a_consent_from_another_clinic_cannot_be_signed(
        self, published_consent_a, patient_b, episode_a
    ):
        with pytest.raises(ValidationError):
            SignedConsent.sign(
                version=published_consent_a, patient=patient_b, episode=episode_a,
                signature_image=signature(),
            )

    def test_the_episode_must_belong_to_the_signer(
        self, published_consent_a, patient_a, patient_b, clinic_a, episode_a
    ):
        """Un consentimiento colgado del episodio de otro es un error de historia."""
        other_patient = type(patient_a).objects.create(
            clinic=clinic_a, first_name='Otro', last_name='Paciente',
            email='otro@example.com', phone='555-0003',
        )
        from clinical.models import Episode

        other_episode = Episode.objects.create(
            history=other_patient.medical_history, reason='Otra cosa',
        )

        with pytest.raises(ValidationError):
            SignedConsent.sign(
                version=published_consent_a, patient=patient_a, episode=other_episode,
                signature_image=signature(),
            )


@pytest.mark.django_db
class TestAudit:
    def test_the_signature_is_recorded_without_leaking_the_text_or_the_key(
        self, published_consent_a, patient_a, episode_a
    ):
        from audit.models import ChangeLog

        consent = SignedConsent.sign(
            version=published_consent_a, patient=patient_a, episode=episode_a,
            signature_image=signature(),
        )

        entry = ChangeLog.objects.filter(
            model_label='clinical.SignedConsent',
            object_id=str(consent.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert entry.patient_id == patient_a.pk
        # QUE firmó, sí; el texto y dónde vive la firma, jamás: el log no puede
        # ser una segunda copia sin cifrar del documento.
        assert CONSENT_TEXT not in str(entry.changes)
        assert consent.signature_image.name not in str(entry.changes)

    def test_publishing_a_version_is_recorded_whole(self, draft_consent_a):
        """El documento en blanco no es dato de paciente: se audita entero."""
        from audit.models import ChangeLog

        draft_consent_a.publish()

        entries = ChangeLog.objects.filter(
            model_label='clinical.ConsentVersion',
            object_id=str(draft_consent_a.pk),
            action=ChangeLog.Action.UPDATE,
        )
        published = [e for e in entries if 'is_published' in e.changes]
        assert published and published[0].changes['is_published']['after'] is True


@pytest.mark.django_db
class TestAdmin:
    def test_consent_pages_are_reachable(self, admin_site_client, signed_consent_a):
        for url in (
            '/admin/clinical/consenttemplate/',
            '/admin/clinical/consentversion/',
            '/admin/clinical/signedconsent/',
            f'/admin/clinical/signedconsent/{signed_consent_a.pk}/change/',
            '/admin/clinical/signedconsent/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_the_admin_links_to_the_protected_view_not_to_the_bucket(
        self, admin_site_client, signed_consent_a
    ):
        response = admin_site_client.get(
            f'/admin/clinical/signedconsent/{signed_consent_a.pk}/change/'
        )
        body = response.content.decode()
        assert reverse('clinical:consent-signature', args=[signed_consent_a.public_id]) in body

    def test_a_signed_consent_cannot_be_deleted_from_the_admin(self, signed_consent_a):
        from django.contrib.admin.sites import site

        model_admin = site._registry[SignedConsent]
        assert model_admin.has_delete_permission(None, obj=signed_consent_a) is False
        assert model_admin.has_change_permission(None, obj=signed_consent_a) is False

    def test_a_published_version_is_read_only(self, published_consent_a):
        from django.contrib.admin.sites import site

        model_admin = site._registry[ConsentVersion]
        readonly = model_admin.get_readonly_fields(None, obj=published_consent_a)

        assert 'text' in readonly
        assert model_admin.has_delete_permission(None, obj=published_consent_a) is False
