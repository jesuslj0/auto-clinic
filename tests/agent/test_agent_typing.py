"""Deducción de «el agente está escribiendo…» (ConversationSession.agent_typing_seconds)."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message


@pytest.fixture
def session(db, clinic_a, patient_a):
    return ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone)


def _say(session, clinic, direction, sender):
    return record_message(clinic=clinic, session=session, direction=direction, sender=sender, body='x')


@pytest.mark.django_db
def test_typing_after_patient_message(session, clinic_a):
    _say(session, clinic_a, ChatMessage.Direction.INBOUND, ChatMessage.Sender.PATIENT)
    session.refresh_from_db()
    assert 0 < session.agent_typing_seconds <= 60


@pytest.mark.django_db
def test_not_typing_once_agent_answered(session, clinic_a):
    _say(session, clinic_a, ChatMessage.Direction.INBOUND, ChatMessage.Sender.PATIENT)
    _say(session, clinic_a, ChatMessage.Direction.OUTBOUND, ChatMessage.Sender.AGENT)
    session.refresh_from_db()
    assert session.agent_typing_seconds == 0


@pytest.mark.django_db
def test_not_typing_when_agent_paused_or_off(session, clinic_a):
    _say(session, clinic_a, ChatMessage.Direction.INBOUND, ChatMessage.Sender.PATIENT)
    session.refresh_from_db()
    session.agent_paused = True
    assert session.agent_typing_seconds == 0
    session.agent_paused = False
    clinic_a.agent_enabled = False
    session.clinic = clinic_a
    assert session.agent_typing_seconds == 0


@pytest.mark.django_db
def test_typing_expires(session, clinic_a):
    _say(session, clinic_a, ChatMessage.Direction.INBOUND, ChatMessage.Sender.PATIENT)
    session.refresh_from_db()
    session.last_interaction = timezone.now() - timedelta(seconds=120)
    assert session.agent_typing_seconds == 0


@pytest.mark.django_db
def test_thread_renders_status(admin_client, admin_user, client, session, clinic_a):
    _say(session, clinic_a, ChatMessage.Direction.INBOUND, ChatMessage.Sender.PATIENT)
    client.force_login(admin_user)
    html = client.get(reverse('agent:chat-thread', args=[session.id])).content.decode()
    assert 'El agente está escribiendo' in html
    assert 'agentTypingSeconds: ' in html
