"""Envío Web Push: payload genérico y limpieza de suscripciones muertas."""
import json
from unittest.mock import patch

import pytest
from pywebpush import WebPushException

from notifications.models import Notification, NotificationPreference, PushSubscription
from notifications import push
from notifications.tasks import purge_old_notifications


@pytest.fixture(autouse=True)
def vapid(settings):
    settings.VAPID_PUBLIC_KEY = 'pub'
    settings.VAPID_PRIVATE_KEY = 'priv'
    settings.VAPID_CLAIMS_EMAIL = 'ops@example.test'


@pytest.fixture
def note(admin_user, clinic_a, patient_a):
    return Notification.objects.create(
        clinic=clinic_a, user=admin_user, kind='chat_new', title='Conversación nueva',
        body='Alguien ha escrito por primera vez.', url='/chats/',
    )


@pytest.fixture
def sub(admin_user):
    return PushSubscription.objects.create(user=admin_user, endpoint='https://push.example/1', p256dh='k', auth='a')


def test_payload_is_generic(note, sub, patient_a):
    with patch.object(push, '_webpush') as wp:
        assert push.deliver([note.pk]) == 1
    data = wp.call_args.kwargs['data']
    assert patient_a.first_name not in data and patient_a.phone not in data
    assert set(json.loads(data)) == {'title', 'body', 'url', 'tag'}


def test_gone_subscription_is_deleted(note, sub):
    error = WebPushException('gone', response=type('R', (), {'status_code': 410})())
    with patch.object(push, '_webpush', side_effect=error):
        assert push.deliver([note.pk]) == 0
    assert PushSubscription.objects.count() == 0


def test_transient_failure_is_counted_not_deleted(note, sub):
    error = WebPushException('boom', response=type('R', (), {'status_code': 500})())
    with patch.object(push, '_webpush', side_effect=error):
        push.deliver([note.pk])
    sub.refresh_from_db()
    assert sub.failures == 1


def test_push_preference_off_sends_nothing(note, sub, admin_user):
    NotificationPreference.objects.create(user=admin_user, kind='chat_new', push=False)
    with patch.object(push, '_webpush') as wp:
        assert push.deliver([note.pk]) == 0
    wp.assert_not_called()


def test_without_keys_push_is_off(note, sub, settings):
    settings.VAPID_PRIVATE_KEY = ''
    with patch.object(push, '_webpush') as wp:
        assert push.deliver([note.pk]) == 0
    wp.assert_not_called()


def test_purge_removes_old_read_and_dismissed(note, admin_user, clinic_a):
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    Notification.objects.filter(pk=note.pk).update(read_at=now - timedelta(days=31))
    fresh = Notification.objects.create(clinic=clinic_a, user=admin_user, kind='chat_new', title='x')
    assert purge_old_notifications() == 1
    assert list(Notification.objects.all()) == [fresh]
