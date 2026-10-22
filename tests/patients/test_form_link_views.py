"""Mandar la ficha al móvil del paciente desde el panel (`patients:form-link-create`).

Lo que se defiende:

1. Solo se pide lo que se puede contestar (versión vigente publicada, de la clínica
   del paciente) y siempre algo.
2. Con la ventana de 24 h abierta sale por WhatsApp; fuera de ella no se manda nada y
   se entrega el enlace para copiarlo. Un fallo de Meta no pierde el enlace.
3. El enlace en claro solo está en la respuesta que lo crea: ni en la ficha ni en la base.
4. Aislamiento por clínica (404), paciente archivado y revocación.
"""
from unittest.mock import patch

import pytest
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message
from agent.whatsapp import WhatsAppError
from audit.models import AccessLog
from clinical.intake import hash_token, issue_link
from clinical.models import ConsentTemplate, PatientFormLink, QuestionnaireTemplate


def create_url(patient):
    return reverse('patients:form-link-create', kwargs={'id': patient.pk})


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def open_window(db, clinic_a, patient_a):
    session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone, patient=patient_a)
    record_message(
        clinic=clinic_a, session=session, direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT, body='Hola',
    )
    return session


@pytest.fixture
def consent_template_a(db, clinic_a):
    template = ConsentTemplate.objects.create(clinic=clinic_a, name='Protección de datos')
    template.new_draft_version(text='Texto del consentimiento').publish()
    return template


def payload(questionnaire=None, consent=None, **extra):
    data = {'episode_reason': 'Primera visita'}
    if questionnaire is not None:
        data['questionnaire'] = questionnaire.pk
    if consent is not None:
        data['consent'] = consent.pk
    data.update(extra)
    return data


@pytest.mark.django_db
class TestPage:
    def test_requires_login(self, client, patient_a):
        response = client.get(create_url(patient_a))
        assert response.status_code == 302 and '/login/' in response['Location']

    def test_offers_only_what_can_be_answered(self, logged, patient_a, questionnaire_template_a, draft_version_a, clinic_a):
        # La plantilla solo tiene un borrador: no hay nada que contestar.
        html = logged.get(create_url(patient_a)).content.decode()
        assert 'No hay nada que enviar' in html

    def test_lists_templates_with_a_current_version(self, logged, patient_a, questionnaire_template_a, published_version_a, consent_template_a):
        html = logged.get(create_url(patient_a)).content.decode()

        assert 'Anamnesis podológica (v1)' in html
        assert 'Protección de datos (v1)' in html

    def test_other_clinics_templates_are_not_offered(self, logged, patient_a, clinic_b, published_version_a):
        other = QuestionnaireTemplate.objects.create(clinic=clinic_b, name='Ajena')
        other.new_draft_version(copy_questions_from=False)
        html = logged.get(create_url(patient_a)).content.decode()
        assert 'Ajena' not in html

    def test_other_clinics_patient_is_a_404(self, logged, patient_b):
        assert logged.get(create_url(patient_b)).status_code == 404
        assert logged.post(create_url(patient_b), {}).status_code == 404

    def test_reading_the_page_is_logged(self, logged, patient_a, published_version_a):
        logged.get(create_url(patient_a))
        assert AccessLog.objects.filter(patient=patient_a, path=create_url(patient_a)).exists()

    def test_says_how_it_will_arrive(self, logged, patient_a, published_version_a, open_window):
        assert 'Se enviará ahora mismo' in logged.get(create_url(patient_a)).content.decode()


@pytest.mark.django_db
class TestSend:
    def test_sends_through_whatsapp_inside_the_window(self, logged, patient_a, questionnaire_template_a, published_version_a, open_window):
        with patch('agent.services.send_text', return_value='wamid.OK') as send:
            response = logged.post(create_url(patient_a), payload(questionnaire_template_a))

        assert response.status_code == 200
        link = PatientFormLink.objects.get()
        assert link.channel == PatientFormLink.Channel.WHATSAPP and link.sent_at is not None
        body = send.call_args.args[2]
        assert '/ficha/' in body and patient_a.first_name in body
        # Ni un dato clínico en un mensaje que atraviesa a Meta.
        assert 'Anamnesis' not in body
        assert ChatMessage.objects.filter(session=open_window, body=body).exists()
        assert 'Enviado por WhatsApp' in response.content.decode()

    def test_the_url_in_the_message_opens_the_page(self, logged, client, patient_a, questionnaire_template_a, published_version_a, open_window):
        with patch('agent.services.send_text', return_value='wamid.OK') as send:
            logged.post(create_url(patient_a), payload(questionnaire_template_a))
        url = [word for word in send.call_args.args[2].split() if '/ficha/' in word][0]

        from django.test import Client
        assert Client().get(url.split('testserver')[1]).status_code == 200

    def test_the_clear_link_is_shown_once_and_never_stored(self, logged, patient_a, questionnaire_template_a, published_version_a, open_window):
        with patch('agent.services.send_text', return_value='wamid.OK') as send:
            response = logged.post(create_url(patient_a), payload(questionnaire_template_a))
        token = [w for w in send.call_args.args[2].split() if '/ficha/' in w][0].rstrip('/').split('/')[-1]

        link = PatientFormLink.objects.get()
        assert link.token_hash == hash_token(token) and token in response.content.decode()
        # La ficha del paciente (donde se listan los enlaces) no lo enseña.
        tab = logged.get(reverse('patients:tab-documents', kwargs={'id': patient_a.pk})).content.decode()
        assert token not in tab and 'Pendiente' in tab

    def test_outside_the_window_nothing_is_sent_and_the_link_is_handed_over(self, logged, patient_a, questionnaire_template_a, published_version_a):
        with patch('agent.services.send_text') as send:
            response = logged.post(create_url(patient_a), payload(questionnaire_template_a))

        send.assert_not_called()
        html = response.content.decode()
        assert 'nunca ha escrito' in html and '/ficha/' in html
        assert PatientFormLink.objects.get().channel == PatientFormLink.Channel.MANUAL

    def test_closed_window_after_24h(self, logged, patient_a, open_window, questionnaire_template_a, published_version_a):
        from datetime import timedelta

        from django.utils import timezone

        open_window.last_interaction = timezone.now() - timedelta(hours=25)
        open_window.save()
        with patch('agent.services.send_text') as send:
            response = logged.post(create_url(patient_a), payload(questionnaire_template_a))

        send.assert_not_called()
        assert 'más de 24 horas' in response.content.decode()

    def test_a_whatsapp_failure_keeps_the_link_usable(self, logged, patient_a, questionnaire_template_a, published_version_a, open_window):
        with patch('agent.services.send_text', side_effect=WhatsAppError('Token caducado')):
            response = logged.post(create_url(patient_a), payload(questionnaire_template_a))

        link = PatientFormLink.objects.get()
        assert link.is_usable() and link.channel == PatientFormLink.Channel.MANUAL
        html = response.content.decode()
        assert 'Token caducado' in html and '/ficha/' in html

    def test_nothing_chosen_is_an_error(self, logged, patient_a, published_version_a):
        response = logged.post(create_url(patient_a), {'episode_reason': 'x'})
        assert response.status_code == 200 and not PatientFormLink.objects.exists()
        assert 'Elige qué se le pide' in response.content.decode()

    def test_a_template_of_another_clinic_is_refused(self, logged, patient_a, clinic_b):
        other = QuestionnaireTemplate.objects.create(clinic=clinic_b, name='Ajena')
        other.new_draft_version(copy_questions_from=False)
        response = logged.post(create_url(patient_a), payload(other))
        assert response.status_code == 200 and not PatientFormLink.objects.exists()

    def test_asks_for_an_episode_reason_when_none_is_chosen(self, logged, patient_a, questionnaire_template_a, published_version_a):
        response = logged.post(create_url(patient_a), {'questionnaire': questionnaire_template_a.pk})
        assert response.status_code == 200 and not PatientFormLink.objects.exists()

    def test_the_chosen_episode_is_kept(self, logged, patient_a, questionnaire_template_a, published_version_a, episode_a):
        logged.post(create_url(patient_a), {'questionnaire': questionnaire_template_a.pk, 'episode': episode_a.pk})
        assert PatientFormLink.objects.get().episode == episode_a

    def test_an_archived_patient_gets_nothing(self, logged, patient_a, questionnaire_template_a, published_version_a):
        patient_a.archive()
        response = logged.post(create_url(patient_a), payload(questionnaire_template_a))
        assert response.status_code == 302 and not PatientFormLink.objects.exists()

    def test_sending_again_revokes_the_previous_link(self, logged, patient_a, questionnaire_template_a, published_version_a):
        logged.post(create_url(patient_a), payload(questionnaire_template_a))
        logged.post(create_url(patient_a), payload(questionnaire_template_a))

        statuses = sorted(link.get_status() for link in PatientFormLink.objects.all())
        assert statuses == ['pending', 'revoked']


@pytest.mark.django_db
class TestRevoke:
    def url(self, patient, link):
        return reverse('patients:form-link-revoke', kwargs={'id': patient.pk, 'link_id': link.pk})

    @pytest.fixture
    def link(self, patient_a, admin_user, questionnaire_template_a, published_version_a):
        return issue_link(patient=patient_a, created_by=admin_user, questionnaire_template=questionnaire_template_a)

    def test_revokes_a_pending_link_and_the_page_dies(self, logged, client, patient_a, link):
        link, token = link
        response = logged.post(self.url(patient_a, link))

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.get_status() == PatientFormLink.Status.REVOKED and link.revoked_by is not None
        from django.test import Client
        assert Client().get(reverse('intake:fill', kwargs={'token': token})).status_code == 404

    def test_get_is_not_allowed(self, logged, patient_a, link):
        assert logged.get(self.url(patient_a, link[0])).status_code == 405

    def test_other_clinics_link_is_a_404(self, client, admin_user_b, patient_a, link):
        client.force_login(admin_user_b)
        assert client.post(self.url(patient_a, link[0])).status_code == 404

    def test_a_link_of_another_patient_is_a_404(self, logged, clinic_a, link):
        from patients.models import Patient

        other = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='Persona')
        assert logged.post(self.url(other, link[0])).status_code == 404
        link[0].refresh_from_db()
        assert link[0].is_usable()


@pytest.mark.django_db
class TestDocumentsTab:
    def test_lists_links_with_their_state(self, logged, patient_a, admin_user, questionnaire_template_a, published_version_a):
        issue_link(patient=patient_a, created_by=admin_user, questionnaire_template=questionnaire_template_a)
        html = logged.get(reverse('patients:tab-documents', kwargs={'id': patient_a.pk})).content.decode()

        assert 'Ficha por enlace' in html and 'Enviar ficha' in html and 'Pendiente' in html and 'Anular' in html

    def test_a_completed_link_shows_who_filled_it(self, logged, patient_a, admin_user, questionnaire_template_a, published_version_a):
        from django.utils import timezone

        link, _ = issue_link(patient=patient_a, created_by=admin_user, questionnaire_template=questionnaire_template_a)
        link.completed_at = timezone.now()
        link.completed_by_name = 'Ana López'
        link.completed_by_relationship = 'Hija'
        link.save()
        html = logged.get(reverse('patients:tab-documents', kwargs={'id': patient_a.pk})).content.decode()

        assert 'Completado' in html and 'Ana López' in html and 'Anular' not in html

    def test_no_send_button_for_an_archived_patient(self, logged, patient_a):
        patient_a.archive()
        html = logged.get(reverse('patients:tab-documents', kwargs={'id': patient_a.pk})).content.decode()
        assert 'Enviar ficha' not in html
