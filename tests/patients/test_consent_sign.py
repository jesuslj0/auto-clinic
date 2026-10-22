"""Firmar un consentimiento desde el panel (`patients:consent-sign`).

Lo que se defiende no es que salga un lienzo, sino las reglas legales:

1. **Se firma el texto vigente y publicado**, y lo firmado queda copiado literal:
   publicar otra versión después no lo reescribe.
2. **Un borrador no se puede firmar**, ni se ofrece.
3. **Se firma lo que se leyó**: si entre abrir la pantalla y firmar se publica otra
   versión, no se firma (el formulario lleva la versión que se enseñó).
4. **Sin firma, o con un lienzo vacío, no hay consentimiento.**
5. El alta va por `SignedConsent.sign()`: la firma acaba en el bucket privado y se
   sirve solo por `clinical:consent-signature`, nunca como URL del bucket.
6. Aislamiento por clínica y lectura con `AccessLog`.
"""
import base64
from io import BytesIO

import pytest
from django.urls import reverse
from PIL import Image, ImageDraw

from audit.models import AccessLog, ChangeLog
from clinical.models import ConsentTemplate, ConsentVersion, Episode, SignedConsent

TEXT_V1 = 'Consiento la cirugía ungueal con anestesia local y conozco sus posibles complicaciones.'
TEXT_V2 = TEXT_V1 + ' Autorizo también el registro fotográfico.'


def sign_url(patient):
    return reverse('patients:consent-sign', kwargs={'id': patient.pk})


def canvas_png(blank=False):
    image = Image.new('RGB', (400, 160), (255, 255, 255))
    if not blank:
        ImageDraw.Draw(image).line([(20, 120), (120, 30), (220, 110), (360, 40)], fill=(15, 23, 42), width=5)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def consent_template(db, clinic_a):
    template = ConsentTemplate.objects.create(clinic=clinic_a, name='Cirugía ungueal', specialty='cirugía')
    version = template.new_draft_version(text=TEXT_V1)
    version.publish()
    template.published = version
    return template


def payload(template, *, episode=None, version=None, signature=None, accepted=True, **extra):
    data = {
        'plantilla': template.pk,
        'version': (version or template.current_version).pk,
        'signature_data': canvas_png() if signature is None else signature,
    }
    if accepted:
        data['accepted'] = 'on'
    if episode is not None:
        data['episode'] = episode.pk
    data.update(extra)
    return data


@pytest.mark.django_db
class TestPage:
    def test_shows_the_literal_text_of_the_current_version(self, logged, patient_a, consent_template):
        html = logged.get(sign_url(patient_a)).content.decode()

        assert TEXT_V1 in html
        assert 'Cirugía ungueal' in html
        assert '<canvas' in html
        assert 'signature_pad.js' in html

    def test_a_draft_version_is_not_offered(self, logged, patient_a, clinic_a):
        template = ConsentTemplate.objects.create(clinic=clinic_a, name='Solo borrador')
        template.new_draft_version(text='Texto sin publicar')

        html = logged.get(sign_url(patient_a)).content.decode()

        assert 'No hay consentimientos disponibles' in html
        assert 'Texto sin publicar' not in html

    def test_shows_the_new_version_not_the_old_one(self, logged, patient_a, consent_template):
        consent_template.new_draft_version(text=TEXT_V2).publish()

        html = logged.get(sign_url(patient_a)).content.decode()

        assert TEXT_V2 in html

    def test_with_several_templates_it_asks_which(self, logged, patient_a, clinic_a, consent_template):
        other = ConsentTemplate.objects.create(clinic=clinic_a, name='Fotografías')
        other.new_draft_version(text='Autorizo fotografías.').publish()

        html = logged.get(sign_url(patient_a)).content.decode()
        assert '¿Qué consentimiento firma?' in html
        assert '<canvas' not in html

        html = logged.get(f'{sign_url(patient_a)}?plantilla={other.pk}').content.decode()
        assert 'Autorizo fotografías.' in html

    def test_another_clinics_template_is_not_offered(self, logged, patient_a, clinic_b):
        template = ConsentTemplate.objects.create(clinic=clinic_b, name='De otra clínica')
        template.new_draft_version(text='Texto ajeno').publish()

        html = logged.get(sign_url(patient_a)).content.decode()

        assert 'Texto ajeno' not in html

    def test_other_clinic_patient_is_404(self, logged, patient_b):
        assert logged.get(sign_url(patient_b)).status_code == 404
        assert not AccessLog.objects.filter(patient=patient_b).exists()

    def test_the_read_is_logged(self, logged, patient_a, consent_template):
        logged.get(sign_url(patient_a))

        assert AccessLog.objects.filter(patient=patient_a).exists()


@pytest.mark.django_db
class TestSigning:
    def test_signs_with_the_current_text_copied_literally(
        self, logged, patient_a, episode_a, consent_template
    ):
        response = logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a))

        consent = SignedConsent.objects.get(patient=patient_a)
        assert consent.text_copy == TEXT_V1
        assert consent.version == consent_template.current_version
        assert consent.episode == episode_a
        assert consent.signature_image
        assert consent.mime_type == 'image/png'
        assert response['Location'] == (
            reverse('patients:tab-documents', kwargs={'id': patient_a.pk}) + '#consentimientos'
        )

    def test_republishing_afterwards_does_not_rewrite_what_was_signed(
        self, logged, patient_a, episode_a, consent_template
    ):
        logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a))
        consent_template.new_draft_version(text=TEXT_V2).publish()

        consent = SignedConsent.objects.get(patient=patient_a)
        assert consent.text_copy == TEXT_V1
        html = logged.get(reverse('patients:tab-consents', kwargs={'id': patient_a.pk})).content.decode()
        assert TEXT_V1 in html and 'registro fotográfico' not in html

    def test_opens_an_episode_when_none_is_chosen(self, logged, patient_a, consent_template):
        logged.post(sign_url(patient_a), payload(consent_template, episode_reason='Cirugía de uña'))

        consent = SignedConsent.objects.get(patient=patient_a)
        assert consent.episode.reason == 'Cirugía de uña'

    def test_needs_an_episode_or_a_reason(self, logged, patient_a, consent_template):
        response = logged.post(sign_url(patient_a), payload(consent_template))

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    @pytest.mark.parametrize('signature', ['', 'data:image/png;base64,'])
    def test_no_signature_no_consent(self, logged, patient_a, episode_a, consent_template, signature):
        response = logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a, signature=signature))

        assert response.status_code == 200
        assert 'Falta la firma' in response.content.decode()
        assert not SignedConsent.objects.exists()

    def test_a_blank_canvas_is_not_a_signature(self, logged, patient_a, episode_a, consent_template):
        response = logged.post(
            sign_url(patient_a), payload(consent_template, episode=episode_a, signature=canvas_png(blank=True))
        )

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    def test_the_patient_must_be_confirmed_to_consent(self, logged, patient_a, episode_a, consent_template):
        response = logged.post(
            sign_url(patient_a), payload(consent_template, episode=episode_a, accepted=False)
        )

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    def test_a_signature_survives_a_form_error(self, logged, patient_a, episode_a, consent_template):
        """Si falla otro campo, la firma vuelve en el formulario: no hay que firmar dos veces."""
        response = logged.post(
            sign_url(patient_a), payload(consent_template, episode=episode_a, accepted=False)
        )

        assert 'name="signature_data"' in response.content.decode()
        assert canvas_png()[:60] in response.content.decode()

    def test_does_not_sign_a_text_that_changed_while_reading(
        self, logged, patient_a, episode_a, consent_template
    ):
        shown = consent_template.current_version
        consent_template.new_draft_version(text=TEXT_V2).publish()

        response = logged.post(
            sign_url(patient_a), payload(consent_template, episode=episode_a, version=shown)
        )

        assert response.status_code == 200
        assert 'ha cambiado mientras se firmaba' in response.content.decode()
        assert not SignedConsent.objects.exists()

    def test_a_draft_version_cannot_be_signed_even_by_forcing_the_post(
        self, logged, patient_a, episode_a, consent_template
    ):
        draft = consent_template.new_draft_version(text='Borrador sin publicar')
        data = payload(consent_template, episode=episode_a)
        data['version'] = draft.pk

        response = logged.post(sign_url(patient_a), data)

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    def test_an_unpublished_template_cannot_be_signed(self, logged, patient_a, episode_a, consent_template):
        consent_template.current_version.unpublish()

        response = logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a, version=ConsentVersion.objects.get()))

        assert not SignedConsent.objects.exists()
        assert response.status_code == 302

    def test_another_patients_episode_cannot_be_used(
        self, logged, patient_a, consent_template, clinic_a, professional_a
    ):
        from patients.models import Patient
        other = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='Persona', phone='+34600111222')
        foreign = Episode.objects.create(history=other.medical_history, reason='Ajeno', responsible_professional=professional_a)

        response = logged.post(sign_url(patient_a), payload(consent_template, episode=foreign))

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    def test_another_clinic_patient_cannot_be_signed_for(self, logged, patient_b, consent_template):
        response = logged.post(sign_url(patient_b), payload(consent_template, episode_reason='x'))

        assert response.status_code == 404
        assert not SignedConsent.objects.exists()

    def test_the_write_is_in_the_changelog_without_the_text(self, logged, patient_a, episode_a, consent_template):
        logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a))

        entries = ChangeLog.objects.filter(patient=patient_a, model_label__icontains='signedconsent')
        assert entries.exists()
        assert TEXT_V1 not in ' '.join(str(entry.changes) for entry in entries)


@pytest.mark.django_db
def test_the_signature_is_served_only_through_the_protected_view(
    logged, patient_a, episode_a, consent_template
):
    logged.post(sign_url(patient_a), payload(consent_template, episode=episode_a))
    consent = SignedConsent.objects.get(patient=patient_a)

    html = logged.get(reverse('patients:tab-consents', kwargs={'id': patient_a.pk})).content.decode()

    assert reverse('clinical:consent-signature', args=[consent.public_id]) in html
    assert consent.signature_image.name not in html
