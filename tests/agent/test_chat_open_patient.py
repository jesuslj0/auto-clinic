"""Botón «Enviar mensaje» / «Ver chat» de la ficha (agent.services.patient_chat_access)."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import patient_chat_access, record_message


def make_thread(clinic, patient, *, last_interaction, is_test=False, with_message=True):
    session = ConversationSession.objects.create(
        clinic=clinic, phone=patient.phone, patient=patient, is_test=is_test
    )
    if with_message:
        record_message(
            clinic=clinic,
            session=session,
            direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT,
            body="Hola",
        )
    session.last_interaction = last_interaction
    session.save(update_fields=['last_interaction'])
    return session


@pytest.mark.django_db
class TestPatientChatAccess:
    def test_no_phone(self, clinic_a, patient_a):
        patient_a.phone = ''
        patient_a.save()
        assert patient_chat_access(patient_a) == (None, 'no_phone')

    def test_never_wrote(self, clinic_a, patient_a):
        assert patient_chat_access(patient_a) == (None, 'never_wrote')

    def test_empty_thread_counts_as_never_wrote(self, clinic_a, patient_a):
        make_thread(clinic_a, patient_a, last_interaction=None, with_message=False)
        assert patient_chat_access(patient_a) == (None, 'never_wrote')

    def test_open_window(self, clinic_a, patient_a):
        session = make_thread(clinic_a, patient_a, last_interaction=timezone.now() - timedelta(hours=2))
        assert patient_chat_access(patient_a) == (session, 'open')

    def test_closed_window(self, clinic_a, patient_a):
        session = make_thread(clinic_a, patient_a, last_interaction=timezone.now() - timedelta(hours=30))
        assert patient_chat_access(patient_a) == (session, 'closed')

    def test_test_thread_is_ignored(self, clinic_a, patient_a):
        make_thread(clinic_a, patient_a, last_interaction=timezone.now(), is_test=True)
        assert patient_chat_access(patient_a) == (None, 'never_wrote')

    def test_does_not_create_sessions(self, clinic_a, patient_a):
        patient_chat_access(patient_a)
        assert not ConversationSession.objects.exists()


@pytest.mark.django_db
class TestPatientDetailChatButton:
    def html(self, client, staff_user, patient):
        client.force_login(staff_user)
        return client.get(reverse('patients:detail', args=[patient.pk])).content.decode()

    def test_open_links_to_thread(self, client, staff_user, clinic_a, patient_a):
        session = make_thread(clinic_a, patient_a, last_interaction=timezone.now())
        html = self.html(client, staff_user, patient_a)
        assert reverse('agent:chat-thread', args=[session.id]) in html
        assert 'Enviar mensaje' in html
        assert 'Ver chat' not in html

    def test_closed_shows_ver_chat(self, client, staff_user, clinic_a, patient_a):
        session = make_thread(clinic_a, patient_a, last_interaction=timezone.now() - timedelta(hours=30))
        html = self.html(client, staff_user, patient_a)
        assert reverse('agent:chat-thread', args=[session.id]) in html
        assert 'Ver chat' in html
        assert 'Ventana de 24 h cerrada' in html

    def test_never_wrote_is_disabled(self, client, staff_user, patient_a):
        html = self.html(client, staff_user, patient_a)
        assert 'Aún no ha escrito' in html
        assert '/chats/' not in html.split('Enviar mensaje')[0][-400:]

    def test_no_phone_is_disabled(self, client, staff_user, patient_a):
        patient_a.phone = ''
        patient_a.save()
        html = self.html(client, staff_user, patient_a)
        assert 'Este paciente no tiene teléfono' in html

    def test_other_clinic_thread_is_not_linked(self, client, staff_user, clinic_b, patient_a):
        make_thread(clinic_b, patient_a, last_interaction=timezone.now())
        html = self.html(client, staff_user, patient_a)
        assert 'Aún no ha escrito' in html
