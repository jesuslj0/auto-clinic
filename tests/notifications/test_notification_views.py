"""Vistas del panel de notificaciones: cada usuario solo ve y toca las suyas."""
import json

import pytest
from django.urls import reverse

from notifications.models import Notification, NotificationPreference, PushSubscription


@pytest.fixture
def mine(admin_user, clinic_a):
    return Notification.objects.create(
        clinic=clinic_a, user=admin_user, kind='chat_new', title='Conversación nueva', url='/chats/'
    )


@pytest.fixture
def theirs(staff_user, clinic_a):
    return Notification.objects.create(
        clinic=clinic_a, user=staff_user, kind='chat_new', title='Conversación nueva', url='/chats/'
    )


@pytest.fixture
def web(client, admin_user):
    client.force_login(admin_user)
    return client


def test_list_shows_only_own(web, mine, theirs):
    response = web.get(reverse('notifications:list'))
    assert list(response.context['notifications']) == [mine]


def test_anonymous_is_redirected_to_login(client, mine):
    response = client.get(reverse('notifications:list'))
    assert response.status_code == 302 and 'login' in response['Location']


def test_open_marks_read_and_redirects(web, mine):
    response = web.get(reverse('notifications:open', args=[mine.pk]))
    assert response.status_code == 302 and response['Location'] == '/chats/'
    mine.refresh_from_db()
    assert mine.read_at is not None


def test_cannot_open_read_or_dismiss_someone_elses(web, theirs):
    assert web.get(reverse('notifications:open', args=[theirs.pk])).status_code == 404
    assert web.post(reverse('notifications:read', args=[theirs.pk])).status_code == 404
    assert web.post(reverse('notifications:dismiss', args=[theirs.pk])).status_code == 404
    theirs.refresh_from_db()
    assert theirs.read_at is None and theirs.dismissed_at is None


def test_read_all_only_touches_own(web, mine, theirs):
    web.post(reverse('notifications:read-all'))
    mine.refresh_from_db(); theirs.refresh_from_db()
    assert mine.read_at is not None and theirs.read_at is None


def test_dismiss_hides_it(web, mine):
    web.post(reverse('notifications:dismiss', args=[mine.pk]))
    assert list(web.get(reverse('notifications:list')).context['notifications']) == []


def test_count_is_per_user(web, mine, theirs):
    data = web.get(reverse('notifications:count')).json()
    assert data['unread'] == 1 and data['latest']['id'] == mine.pk


def test_open_redirect_outside_site_is_refused(web, admin_user, clinic_a):
    n = Notification.objects.create(clinic=clinic_a, user=admin_user, kind='chat_new', title='x', url='https://evil.test/')
    assert web.get(reverse('notifications:open', args=[n.pk]))['Location'] == reverse('notifications:list')


def test_preferences_are_saved_per_user(web, admin_user):
    web.post(reverse('notifications:preferences'), {'in_app_chat_new': 'on'})
    pref = NotificationPreference.objects.get(user=admin_user, kind='chat_new')
    assert pref.in_app and not pref.push
    assert not NotificationPreference.objects.get(user=admin_user, kind='chat_message').in_app


def test_staff_does_not_see_admin_only_category(client, staff_user):
    client.force_login(staff_user)
    kinds = [r['kind'] for r in client.get(reverse('notifications:preferences')).context['rows']]
    assert 'template_status' not in kinds


def test_subscribe_requires_vapid_and_https(web, settings, admin_user):
    body = {'endpoint': 'https://push.example/abc', 'keys': {'p256dh': 'k', 'auth': 'a'}}
    url = reverse('notifications:push-subscribe')
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = ''
    assert web.post(url, json.dumps(body), content_type='application/json').status_code == 409
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = 'x'
    bad = {**body, 'endpoint': 'http://insecure.example/abc'}
    assert web.post(url, json.dumps(bad), content_type='application/json').status_code == 400
    assert web.post(url, json.dumps(body), content_type='application/json').status_code == 201
    assert PushSubscription.objects.get().user == admin_user
    web.post(reverse('notifications:push-unsubscribe'), json.dumps({'endpoint': body['endpoint']}), content_type='application/json')
    assert PushSubscription.objects.count() == 0


def test_service_worker_and_manifest(client):
    sw = client.get('/sw.js')
    assert sw.status_code == 200 and sw['Service-Worker-Allowed'] == '/' and sw['Cache-Control'] == 'no-cache'
    manifest = client.get('/manifest.webmanifest').json()
    assert manifest['display'] == 'standalone' and manifest['icons']
