"""El teléfono de las sesiones de conversación se normaliza a E.164 en la API."""
import pytest

from agent.models import ConversationSession

URL = '/api/agent/sessions/'


@pytest.mark.django_db
class TestSessionPhoneNormalization:
    def test_create_normalizes_phone(self, admin_client, clinic_a):
        resp = admin_client.post(URL, {'clinic': clinic_a.pk, 'phone': '600 11 22 33'}, format='json')
        assert resp.status_code == 201, resp.content
        assert ConversationSession.objects.get().phone == '+34600112233'

    def test_create_rejects_invalid_phone(self, admin_client, clinic_a):
        resp = admin_client.post(URL, {'clinic': clinic_a.pk, 'phone': 'abc'}, format='json')
        assert resp.status_code == 400

    def test_create_duplicate_detected_after_normalizing(self, admin_client, clinic_a):
        ConversationSession.objects.create(clinic=clinic_a, phone='+34600112233')
        resp = admin_client.post(URL, {'clinic': clinic_a.pk, 'phone': '600112233'}, format='json')
        assert resp.status_code == 400

    @pytest.mark.parametrize('query', ['+34600112233', '34600112233', '600112233', ' 34600112233'])
    def test_filter_normalizes_phone(self, admin_client, clinic_a, query):
        ConversationSession.objects.create(clinic=clinic_a, phone='+34600112233')
        ConversationSession.objects.create(clinic=clinic_a, phone='+34611111111')
        resp = admin_client.get(URL, {'phone': query})
        assert resp.status_code == 200
        results = resp.json().get('results', resp.json())
        assert [r['phone'] for r in results] == ['+34600112233']

    def test_filter_invalid_phone_returns_nothing(self, admin_client, clinic_a):
        ConversationSession.objects.create(clinic=clinic_a, phone='+34600112233')
        resp = admin_client.get(URL, {'phone': 'abc'})
        results = resp.json().get('results', resp.json())
        assert results == []
