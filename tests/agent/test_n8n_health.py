"""Botón «comprobar n8n» (agent.views.N8nHealthView)."""
import io
import urllib.error
from unittest import mock

import pytest
from django.urls import reverse

URL = 'agent:n8n-health'


def _resp(body: bytes):
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


@pytest.fixture
def logged(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.mark.django_db
def test_requires_login(client):
    assert client.get(reverse(URL)).status_code == 302


@pytest.mark.django_db
def test_ok(logged):
    with mock.patch('agent.views.urllib.request.urlopen', return_value=_resp(b'{"status":"ok"}')):
        r = logged.get(reverse(URL))
    assert r.json()['ok'] is True
    assert r['Cache-Control'] == 'no-store'


@pytest.mark.django_db
def test_unexpected_body_is_not_ok(logged):
    with mock.patch('agent.views.urllib.request.urlopen', return_value=_resp(b'<html>login</html>')):
        assert logged.get(reverse(URL)).json()['ok'] is False


@pytest.mark.django_db
@pytest.mark.parametrize('exc', [
    urllib.error.HTTPError('u', 502, 'bad', {}, io.BytesIO()),
    urllib.error.URLError('down'),
    TimeoutError(),
])
def test_failures_are_down(logged, exc):
    with mock.patch('agent.views.urllib.request.urlopen', side_effect=exc):
        data = logged.get(reverse(URL)).json()
    assert data['ok'] is False and data['detail']
