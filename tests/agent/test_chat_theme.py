"""Tema de chat por conversación: selector, clases de burbuja y reglas CSS."""
import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message

THEMES = ['lila', 'azul', 'verde', 'naranja', 'rosa', 'grafito']


@pytest.fixture
def session(db, clinic_a, patient_a):
    session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone, patient=patient_a)
    record_message(
        clinic=clinic_a, session=session, direction=ChatMessage.Direction.INBOUND,
        sender=ChatMessage.Sender.PATIENT, body="Hola",
    )
    return session


def make_message(session, *, direction, sender):
    return ChatMessage.objects.create(
        session=session, clinic=session.clinic, direction=direction, sender=sender, body="texto",
    )


@pytest.mark.django_db
class TestThreadTheme:
    def html(self, client, staff_user, session):
        client.force_login(staff_user)
        return client.get(reverse('agent:chat-thread', args=[session.id])).content.decode()

    def test_thread_has_picker_and_theme_root(self, client, staff_user, session):
        html = self.html(client, staff_user, session)
        assert 'data-chat-theme-root' in html
        assert f"chatThemePicker('{session.id}')" in html
        assert 'chat-thread-bg' in html
        assert f'ac-chat-theme:{session.id}' in html  # script que aplica el guardado

    def test_picker_lists_all_themes(self, client, staff_user, session):
        html = self.html(client, staff_user, session)
        for theme in THEMES:
            assert f"choose('{theme}')" in html

    def test_theme_rules_exist_for_every_non_default_theme(self, client, staff_user, session):
        html = self.html(client, staff_user, session)
        for theme in THEMES[1:]:
            assert f'[data-chat-theme="{theme}"]' in html
            assert f'.dark [data-chat-theme="{theme}"]' in html

    def test_inbox_without_thread_has_no_picker(self, client, staff_user, session):
        client.force_login(staff_user)
        html = client.get(reverse('agent:chat-inbox')).content.decode()
        assert 'chatThemePicker(' not in html


@pytest.mark.django_db
class TestBubbleClasses:
    def render(self, message):
        return render_to_string('agent/_message_bubble.html', {'message': message})

    def test_agent_bubble_uses_theme_colour(self, session):
        html = self.render(make_message(session, direction='outbound', sender='agent'))
        assert 'chat-bubble-agent' in html
        assert '--bubble-tail' not in html

    def test_staff_bubble_uses_theme_colour(self, session):
        html = self.render(make_message(session, direction='outbound', sender='staff'))
        assert 'chat-bubble-staff' in html
        assert '--bubble-tail' not in html

    def test_inbound_bubble_is_untouched(self, session):
        html = self.render(make_message(session, direction='inbound', sender='patient'))
        assert 'chat-bubble-in' in html
        assert 'chat-bubble-agent' not in html and 'chat-bubble-staff' not in html
