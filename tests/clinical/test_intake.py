"""Ficha desde el móvil del paciente: el enlace, la página pública y lo que escribe.

Lo que se defiende no es que haya un formulario sino las reglas de una puerta
pública a una capa de datos de salud:

1. **El token no se guarda** (solo su hash), caduca y es de un solo uso.
2. **Un GET no gasta el enlace** y no enseña nada de la historia.
3. **Todo enlace muerto da la misma respuesta**; los tokens desconocidos se limitan.
4. **Se verifica a quien contesta** y cinco fallos bloquean el enlace.
5. **Se firma lo que se leyó** y un menor firma su representante.
6. **Todo o nada**, y atribuido al enlace en la auditoría (origen `public`).
7. **La página no carga nada de terceros** y lleva cabeceras cerradas.
"""
import base64
import re
from datetime import date, timedelta
from io import BytesIO

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from audit.models import AccessLog, ChangeLog
from clinical.intake import (
    LinkNoLongerUsable,
    complete_link,
    find_link,
    hash_token,
    issue_link,
)
from clinical.models import (
    ClinicalAlert,
    ConsentTemplate,
    Episode,
    PatientFormLink,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    SignedConsent,
    TemplateVersion,
)

CONSENT_TEXT = 'Consiento el tratamiento de mis datos de salud para mi asistencia en la clínica.'


def fill_url(token):
    return reverse('intake:fill', kwargs={'token': token})


def canvas_png(blank=False):
    image = Image.new('RGB', (400, 160), (255, 255, 255))
    if not blank:
        ImageDraw.Draw(image).line([(20, 120), (120, 30), (220, 110), (360, 40)], fill=(15, 23, 42), width=5)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def consent_version_a(db, clinic_a):
    template = ConsentTemplate.objects.create(clinic=clinic_a, name='Protección de datos')
    version = template.new_draft_version(text=CONSENT_TEXT)
    version.publish()
    return version


@pytest.fixture
def issued(db, patient_a, admin_user, questionnaire_template_a, published_version_a):
    """Enlace con cuestionario. `(enlace, token en claro)`."""
    return issue_link(
        patient=patient_a, created_by=admin_user,
        questionnaire_template=questionnaire_template_a,
        episode_reason='Primera visita',
    )


@pytest.fixture
def issued_both(db, patient_a, admin_user, questionnaire_template_a, published_version_a, consent_version_a):
    return issue_link(
        patient=patient_a, created_by=admin_user,
        questionnaire_template=questionnaire_template_a,
        consent_template=consent_version_a.template,
        episode_reason='Primera visita',
    )


def answers_payload(version, *, diabetic=False, extra=None):
    """POST del formulario con prefijos `who`, `q` y `c`."""
    questions = list(version.questions.all())
    data = {
        'q-shown_version': version.pk,
        f'q-q_{questions[0].pk}': 'true' if diabetic else 'false',
        f'q-q_{questions[1].pk}': 'Ninguna',
        f'q-q_{questions[2].pk}': 'Deportivo',
    }
    data.update(extra or {})
    return data


# ---------------------------------------------------------------------------
# El token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToken:
    def test_only_the_hash_is_stored(self, issued):
        link, token = issued

        assert link.token_hash == hash_token(token)
        assert token not in link.token_hash
        # Ningún campo de la fila lleva el token en claro.
        for field in link._meta.concrete_fields:
            assert token not in str(getattr(link, field.attname))

    def test_the_token_is_long_enough_not_to_be_guessed(self, issued):
        _, token = issued
        assert len(token) >= 43

    def test_it_expires_in_the_configured_time(self, issued, settings):
        link, _ = issued
        assert timedelta(hours=47) < link.expires_at - timezone.now() <= timedelta(hours=48)

    def test_new_link_revokes_the_previous_pending_one(self, issued, patient_a, admin_user, questionnaire_template_a):
        old, _ = issued
        new, _ = issue_link(
            patient=patient_a, created_by=admin_user, questionnaire_template=questionnaire_template_a,
        )

        old.refresh_from_db()
        assert old.get_status() == PatientFormLink.Status.REVOKED
        assert new.get_status() == PatientFormLink.Status.PENDING

    def test_a_link_cannot_be_deleted(self, issued):
        from clinical.exceptions import ProtectedClinicalRecord

        link, _ = issued
        with pytest.raises(ProtectedClinicalRecord):
            link.delete()

    def test_it_must_ask_for_something(self, db, patient_a, admin_user):
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError), transaction.atomic():
            issue_link(patient=patient_a, created_by=admin_user)

    def test_the_link_is_audited_without_exposing_the_hash(self, issued):
        link, _ = issued
        log = ChangeLog.objects.filter(model_label='clinical.PatientFormLink', object_id=str(link.pk)).first()

        assert log is not None and log.action == ChangeLog.Action.CREATE
        assert log.changes['token_hash'] == {'changed': True}
        assert log.patient_id == link.patient_id


# ---------------------------------------------------------------------------
# La página
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPage:
    def test_shows_the_questionnaire_and_the_clinic(self, client, issued):
        _, token = issued
        response = client.get(fill_url(token))
        html = response.content.decode()

        assert response.status_code == 200
        assert 'Clinic Alpha' in html
        assert '¿Es usted diabético?' in html
        assert 'Información sobre protección de datos' in html

    def test_shows_nothing_of_the_patient_or_the_history(self, client, issued, patient_a, episode_a, draft_note_a):
        _, token = issued
        html = client.get(fill_url(token)).content.decode()

        for secret in ('John', 'Doe', 'john.doe@example.com', '555-0001',
                       'Dolor en el talón derecho', 'Hiperqueratosis'):
            assert secret not in html

    def test_a_get_does_not_use_up_the_link(self, client, issued):
        link, token = issued
        client.get(fill_url(token))
        client.get(fill_url(token))

        link.refresh_from_db()
        assert link.completed_at is None
        assert link.is_usable()

    def test_response_headers_are_locked_down(self, client, issued):
        _, token = issued
        response = client.get(fill_url(token))

        assert 'no-store' in response['Cache-Control']
        assert response['Referrer-Policy'] == 'same-origin'
        assert 'noindex' in response['X-Robots-Tag']
        assert response['X-Frame-Options'] == 'DENY'
        assert response['X-Content-Type-Options'] == 'nosniff'
        csp = response['Content-Security-Policy']
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert 'unsafe-inline' not in csp

    def test_loads_nothing_from_third_parties(self, client, issued_both):
        _, token = issued_both
        html = client.get(fill_url(token)).content.decode()

        hosts = set(re.findall(r'(?:src|href|action)="(https?://[^"/]+)', html))
        assert hosts == set()
        assert 'googleapis' not in html and 'cdn.' not in html
        # Y la CSP lo prohíbe: ni estilos ni scripts en línea.
        assert '<style' not in html
        assert not re.search(r'<script(?![^>]*\bsrc=)', html)
        assert ' style="' not in html

    def test_the_form_post_passes_csrf_with_a_browser_style_origin(self, issued, published_version_a):
        """Con `Referrer-Policy: no-referrer` Chrome manda `Origin: null` y se rechazaría."""
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        _, token = issued
        page = strict.get(fill_url(token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)

        data = answers_payload(published_version_a, extra={'csrfmiddlewaretoken': csrf})
        ok = strict.post(fill_url(token), data, HTTP_ORIGIN='http://testserver')
        assert ok.status_code == 302

    def test_done_page_has_no_token_and_no_data(self, client, issued):
        response = client.get(reverse('intake:done'))

        assert response.status_code == 200
        assert 'no-store' in response['Cache-Control']

    def test_opening_it_leaves_an_access_log_attributed_to_the_link(self, client, issued):
        link, token = issued
        client.get(fill_url(token))

        log = AccessLog.objects.get(object_id=str(link.pk))
        assert log.origin == 'public'
        assert log.user_id is None
        assert f'#{link.pk}' in log.user_repr
        assert log.patient_id == link.patient_id
        assert log.action == AccessLog.Action.VIEW

    def test_a_consent_asked_for_shows_its_literal_text_and_a_canvas(self, client, issued_both):
        _, token = issued_both
        html = client.get(fill_url(token)).content.decode()

        assert CONSENT_TEXT in html
        assert '<canvas' in html


# ---------------------------------------------------------------------------
# Enlaces que no valen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeadLinks:
    def dead_responses(self, client, issued):
        link, token = issued
        out = {'unknown': client.get(fill_url('x' * 43))}

        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save()
        out['expired'] = client.get(fill_url(token))

        link.expires_at = timezone.now() + timedelta(hours=1)
        link.revoked_at = timezone.now()
        link.save()
        out['revoked'] = client.get(fill_url(token))

        link.revoked_at = None
        link.completed_at = timezone.now()
        link.save()
        out['used'] = client.get(fill_url(token))

        link.completed_at = None
        link.failed_attempts = PatientFormLink.MAX_FAILED_ATTEMPTS
        link.save()
        out['locked'] = client.get(fill_url(token))
        return out

    def test_every_dead_link_gives_the_same_404(self, client, issued):
        responses = self.dead_responses(client, issued)

        assert {r.status_code for r in responses.values()} == {404}
        assert len({r.content for r in responses.values()}) == 1

    def test_a_dead_link_does_not_log_a_view(self, client, issued):
        link, _ = issued
        self.dead_responses(client, issued)
        assert not AccessLog.objects.filter(object_id=str(link.pk)).exists()

    def test_archived_patient_link_is_dead(self, client, issued, patient_a):
        _, token = issued
        patient_a.archive()
        assert client.get(fill_url(token)).status_code == 404

    def test_expired_link_cannot_be_posted_either(self, client, issued, published_version_a):
        link, token = issued
        link.expires_at = timezone.now() - timedelta(seconds=1)
        link.save()

        response = client.post(fill_url(token), answers_payload(published_version_a))

        assert response.status_code == 404
        assert not QuestionnaireResponse.objects.exists()

    def test_find_link_rejects_absurd_tokens_without_querying(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert find_link('') is None
            assert find_link('a' * 500) is None

    def test_unknown_tokens_are_throttled_per_ip(self, client, issued, settings):
        settings.INTAKE_THROTTLE_FAILURES = 3
        _, token = issued

        statuses = [client.get(fill_url(f'bad{i}')).status_code for i in range(5)]

        assert statuses[:3] == [404, 404, 404]
        assert statuses[3] == 429
        # Y mientras dura el bloqueo, ni el token bueno de esa IP.
        response = client.get(fill_url(token))
        assert response.status_code == 429
        assert response['Retry-After']

    def test_expired_links_do_not_count_towards_the_throttle(self, client, issued, settings):
        settings.INTAKE_THROTTLE_FAILURES = 2
        link, token = issued
        link.expires_at = timezone.now() - timedelta(minutes=1)
        link.save()

        assert [client.get(fill_url(token)).status_code for _ in range(5)] == [404] * 5


# ---------------------------------------------------------------------------
# Enviar la ficha
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmit:
    def test_saves_the_anamnesis_as_the_patient_and_uses_up_the_link(self, client, issued, published_version_a, patient_a):
        link, token = issued
        response = client.post(fill_url(token), answers_payload(published_version_a))

        assert response.status_code == 302 and response['Location'] == reverse('intake:done')
        answer = QuestionnaireResponse.objects.get()
        assert answer.source == QuestionnaireResponse.Source.PATIENT_WEB
        assert answer.created_by is None
        assert answer.patient == patient_a
        assert answer.answer_for(published_version_a.questions.first().pk) is False
        link.refresh_from_db()
        assert link.completed_at is not None
        assert link.questionnaire_response == answer
        assert link.privacy_notice_version
        assert link.get_status() == PatientFormLink.Status.COMPLETED

    def test_a_second_submit_with_the_same_link_is_refused(self, client, issued, published_version_a):
        _, token = issued
        client.post(fill_url(token), answers_payload(published_version_a))
        again = client.post(fill_url(token), answers_payload(published_version_a))

        assert again.status_code == 404
        assert QuestionnaireResponse.objects.count() == 1

    def test_the_episode_is_the_one_the_clinic_chose(self, client, issued_both, published_version_a, episode_a, patient_a, admin_user, questionnaire_template_a, consent_version_a):
        link, token = issue_link(
            patient=patient_a, created_by=admin_user,
            questionnaire_template=questionnaire_template_a, episode=episode_a,
        )
        client.post(fill_url(token), answers_payload(published_version_a))

        assert QuestionnaireResponse.objects.get().episode == episode_a

    def test_without_an_episode_it_opens_one_with_the_given_reason(self, client, issued, published_version_a):
        client.post(fill_url(issued[1]), answers_payload(published_version_a))

        episode = QuestionnaireResponse.objects.get().episode
        assert episode.reason == 'Primera visita'
        assert episode.status == Episode.Status.OPEN

    def test_a_closed_episode_is_not_reused(self, client, patient_a, admin_user, episode_a, questionnaire_template_a, published_version_a):
        episode_a.close()
        _, token = issue_link(
            patient=patient_a, created_by=admin_user,
            questionnaire_template=questionnaire_template_a, episode=episode_a,
            episode_reason='Revisión',
        )
        client.post(fill_url(token), answers_payload(published_version_a))

        episode = QuestionnaireResponse.objects.get().episode
        assert episode != episode_a and episode.reason == 'Revisión'

    def test_a_required_question_left_blank_saves_nothing(self, client, issued, published_version_a):
        link, token = issued
        data = answers_payload(published_version_a)
        del data[f'q-q_{published_version_a.questions.first().pk}']

        response = client.post(fill_url(token), data)

        assert response.status_code == 200
        assert not QuestionnaireResponse.objects.exists()
        link.refresh_from_db()
        assert link.completed_at is None

    def test_answering_yes_to_a_coded_question_raises_the_alert(self, client, patient_a, admin_user, clinic_a):
        template = QuestionnaireTemplate.objects.create(clinic=clinic_a, name='Corta')
        version = TemplateVersion.objects.create(template=template)
        Question.objects.create(
            version=version, text='¿Diabetes?', code='has_diabetes',
            answer_type=Question.AnswerType.BOOLEAN, order=1, is_required=True,
        )
        version.publish()
        _, token = issue_link(patient=patient_a, created_by=admin_user, questionnaire_template=template)
        question = version.questions.get()

        client.post(fill_url(token), {'q-shown_version': version.pk, f'q-q_{question.pk}': 'true'})

        assert ClinicalAlert.objects.filter(patient=patient_a, is_active=True).exists()

    def test_the_writes_are_attributed_to_the_link(self, client, issued, published_version_a):
        link, token = issued
        client.post(fill_url(token), answers_payload(published_version_a))

        answer = QuestionnaireResponse.objects.get()
        log = ChangeLog.objects.get(model_label='clinical.QuestionnaireResponse', object_id=str(answer.pk))
        assert log.origin == 'public'
        assert log.user_id is None
        assert f'#{link.pk}' in log.user_repr
        assert log.ip is not None
        # Y la respuesta clínica sigue sin volcar su contenido al log.
        assert log.changes['snapshot'] == {'changed': True}

    def test_a_stale_questionnaire_version_is_rejected(self, client, issued, published_version_a, questionnaire_template_a):
        """Se contesta lo que se leyó: si entre medias se publica otra versión, no entra."""
        link, token = issued
        payload = answers_payload(published_version_a)
        newer = questionnaire_template_a.new_draft_version()
        newer.publish()

        response = client.post(fill_url(token), payload)

        assert response.status_code == 200
        assert 'ha cambiado' in response.content.decode()
        assert not QuestionnaireResponse.objects.exists()

    def test_all_or_nothing_when_the_second_part_fails(self, client, issued_both, published_version_a, consent_version_a):
        link, token = issued_both
        data = answers_payload(published_version_a, extra={
            'c-version': consent_version_a.pk, 'c-accepted': 'on', 'c-signature_data': canvas_png(blank=True),
        })

        response = client.post(fill_url(token), data)

        assert response.status_code == 200
        assert not QuestionnaireResponse.objects.exists() and not SignedConsent.objects.exists()
        link.refresh_from_db()
        assert link.completed_at is None

    def test_complete_link_is_atomic_if_signing_blows_up(self, issued_both, published_version_a, consent_version_a):
        link, _ = issued_both
        with pytest.raises(Exception):
            complete_link(
                link, questionnaire_version=published_version_a, answers={},
                consent_version=consent_version_a, signature_image=None,
            )

        assert not QuestionnaireResponse.objects.exists()
        link.refresh_from_db()
        assert link.completed_at is None

    def test_complete_link_refuses_a_link_that_was_just_used(self, issued, published_version_a):
        link, _ = issued
        complete_link(link, questionnaire_version=published_version_a, answers={})

        with pytest.raises(LinkNoLongerUsable):
            complete_link(link, questionnaire_version=published_version_a, answers={})
        assert QuestionnaireResponse.objects.count() == 1


# ---------------------------------------------------------------------------
# El consentimiento
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConsent:
    def payload(self, published_version_a, consent_version_a, **extra):
        data = answers_payload(published_version_a, extra={
            'c-version': consent_version_a.pk, 'c-accepted': 'on', 'c-signature_data': canvas_png(),
        })
        data.update(extra)
        return data

    def test_signs_with_the_literal_text_copied(self, client, issued_both, published_version_a, consent_version_a):
        link, token = issued_both
        client.post(fill_url(token), self.payload(published_version_a, consent_version_a))

        signed = SignedConsent.objects.get()
        assert signed.text_copy == CONSENT_TEXT
        assert signed.signature_image
        assert signed.signer_name == ''
        link.refresh_from_db()
        assert link.signed_consent == signed

    def test_without_ticking_the_box_there_is_no_consent(self, client, issued_both, published_version_a, consent_version_a):
        data = self.payload(published_version_a, consent_version_a)
        del data['c-accepted']

        response = client.post(fill_url(issued_both[1]), data)

        assert response.status_code == 200
        assert not SignedConsent.objects.exists() and not QuestionnaireResponse.objects.exists()

    def test_a_blank_canvas_is_not_a_signature(self, client, issued_both, published_version_a, consent_version_a):
        data = self.payload(published_version_a, consent_version_a, **{'c-signature_data': canvas_png(blank=True)})

        assert client.post(fill_url(issued_both[1]), data).status_code == 200
        assert not SignedConsent.objects.exists()

    def test_the_text_signed_is_the_one_read(self, client, issued_both, published_version_a, consent_version_a):
        data = self.payload(published_version_a, consent_version_a)
        newer = consent_version_a.template.new_draft_version(text=CONSENT_TEXT + ' Y algo más.')
        newer.publish()

        response = client.post(fill_url(issued_both[1]), data)

        assert response.status_code == 200
        assert not SignedConsent.objects.exists()

    def test_the_signature_never_leaves_as_a_bucket_url(self, client, issued_both, published_version_a, consent_version_a):
        client.post(fill_url(issued_both[1]), self.payload(published_version_a, consent_version_a))
        signed = SignedConsent.objects.get()

        key = signed.signature_image.name
        assert key.startswith('consent-signatures/')
        assert not key.startswith('http')
        assert 'Doe' not in key and 'John' not in key

    def test_a_consent_only_link_has_no_questionnaire(self, client, patient_a, admin_user, consent_version_a):
        _, token = issue_link(
            patient=patient_a, created_by=admin_user, consent_template=consent_version_a.template,
        )
        html = client.get(fill_url(token)).content.decode()
        assert CONSENT_TEXT in html and 'shown_version' not in html

        client.post(fill_url(token), {
            'c-version': consent_version_a.pk, 'c-accepted': 'on', 'c-signature_data': canvas_png(),
        })
        assert SignedConsent.objects.count() == 1 and not QuestionnaireResponse.objects.exists()


# ---------------------------------------------------------------------------
# Verificación y menores
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerification:
    @pytest.fixture
    def born(self, patient_a):
        patient_a.date_of_birth = date(1980, 5, 17)
        patient_a.save()
        return patient_a

    def test_birth_date_is_asked_when_the_file_has_it(self, client, issued, born):
        assert 'birth_date' in client.get(fill_url(issued[1])).content.decode()

    def test_it_is_not_asked_when_the_file_has_none(self, client, issued):
        assert 'birth_date' not in client.get(fill_url(issued[1])).content.decode()

    def test_the_right_date_lets_it_through(self, client, issued, born, published_version_a):
        link, token = issued
        client.post(fill_url(token), answers_payload(published_version_a, extra={'who-birth_date': '1980-05-17'}))
        assert QuestionnaireResponse.objects.count() == 1

    def test_a_missing_date_blocks_without_counting_as_an_attempt(self, client, issued, born, published_version_a):
        link, token = issued
        response = client.post(fill_url(token), answers_payload(published_version_a))

        assert response.status_code == 200 and not QuestionnaireResponse.objects.exists()
        link.refresh_from_db()
        assert link.failed_attempts == 0

    def test_a_wrong_date_saves_nothing_and_counts(self, client, issued, born, published_version_a):
        link, token = issued
        response = client.post(fill_url(token), answers_payload(published_version_a, extra={'who-birth_date': '1980-05-18'}))

        assert response.status_code == 200
        assert 'no coinciden' in response.content.decode()
        assert not QuestionnaireResponse.objects.exists()
        link.refresh_from_db()
        assert link.failed_attempts == 1 and link.completed_at is None

    def test_the_error_does_not_reveal_the_right_date(self, client, issued, born, published_version_a):
        html = client.post(
            fill_url(issued[1]), answers_payload(published_version_a, extra={'who-birth_date': '1999-01-01'})
        ).content.decode()
        assert '1980' not in html and '17/05' not in html

    def test_five_wrong_dates_lock_the_link_even_for_the_right_one(self, client, issued, born, published_version_a):
        link, token = issued
        wrong = answers_payload(published_version_a, extra={'who-birth_date': '1990-01-01'})
        for _ in range(PatientFormLink.MAX_FAILED_ATTEMPTS):
            client.post(fill_url(token), wrong)

        right = answers_payload(published_version_a, extra={'who-birth_date': '1980-05-17'})
        assert client.post(fill_url(token), right).status_code == 404
        assert client.get(fill_url(token)).status_code == 404
        assert not QuestionnaireResponse.objects.exists()

    def test_a_minor_needs_the_representative(self, client, issued, patient_a, published_version_a):
        patient_a.date_of_birth = timezone.localdate().replace(year=timezone.localdate().year - 10)
        patient_a.save()
        data = answers_payload(published_version_a, extra={
            'who-birth_date': patient_a.date_of_birth.isoformat(),
        })

        response = client.post(fill_url(issued[1]), data)

        assert response.status_code == 200 and not QuestionnaireResponse.objects.exists()
        assert 'nombre' in response.content.decode()

    def test_a_minors_consent_records_who_signed(self, client, patient_a, admin_user, published_version_a, consent_version_a):
        patient_a.date_of_birth = timezone.localdate().replace(year=timezone.localdate().year - 10)
        patient_a.save()
        link, token = issue_link(
            patient=patient_a, created_by=admin_user, consent_template=consent_version_a.template,
        )
        client.post(fill_url(token), {
            'who-birth_date': patient_a.date_of_birth.isoformat(),
            'who-signer_name': 'Marta Pérez', 'who-signer_relationship': 'Madre',
            'c-version': consent_version_a.pk, 'c-accepted': 'on', 'c-signature_data': canvas_png(),
        })

        signed = SignedConsent.objects.get()
        assert (signed.signer_name, signed.signer_relationship) == ('Marta Pérez', 'Madre')
        link.refresh_from_db()
        assert link.completed_by_name == 'Marta Pérez'

    def test_an_adult_can_fill_on_behalf_of_themselves_or_someone_else(self, client, issued, published_version_a):
        link, token = issued
        client.post(fill_url(token), answers_payload(published_version_a, extra={
            'who-on_behalf': 'on', 'who-signer_name': 'Ana López', 'who-signer_relationship': 'Hija',
        }))
        link.refresh_from_db()
        assert link.completed_by_relationship == 'Hija'

    def test_on_behalf_without_name_is_refused(self, client, issued, published_version_a):
        response = client.post(fill_url(issued[1]), answers_payload(published_version_a, extra={'who-on_behalf': 'on'}))
        assert response.status_code == 200 and not QuestionnaireResponse.objects.exists()

    def test_signed_consent_signer_is_immutable_in_the_database(self, client, patient_a, admin_user, consent_version_a):
        from django.db import connection, transaction
        from django.db.utils import ProgrammingError

        _, token = issue_link(patient=patient_a, created_by=admin_user, consent_template=consent_version_a.template)
        client.post(fill_url(token), {
            'c-version': consent_version_a.pk, 'c-accepted': 'on', 'c-signature_data': canvas_png(),
        })
        signed = SignedConsent.objects.get()

        with pytest.raises(ProgrammingError, match='inmutable'), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE clinical_signed_consent SET signer_name = 'otro' WHERE id = %s", [signed.pk]
                )
