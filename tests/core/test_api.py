"""Tests for core API endpoints: /api/clinics/ and /api/users/."""
import pytest
from django.test import override_settings
from django.urls import reverse

from core.models import Clinic, User

MASTER_KEY = "test-master-secret-key"
AGENT_CONFIG_SETTINGS = {"AGENT_MASTER_API_KEY": MASTER_KEY}


@pytest.mark.django_db
class TestClinicViewSet:
    def test_list_unauthenticated_denied(self, api_client):
        url = "/api/clinics/"
        response = api_client.get(url)
        assert response.status_code in (401, 403)

    def test_list_authenticated(self, admin_client, clinic_a):
        response = admin_client.get("/api/clinics/")
        assert response.status_code == 200

    def test_create_clinic_as_admin(self, admin_client):
        data = {"clinic_id": "new-clinic", "name": "New Clinic"}
        response = admin_client.post("/api/clinics/", data)
        assert response.status_code == 201

    def test_create_clinic_as_staff_denied(self, staff_client):
        data = {"name": "New Clinic", "slug": "new-clinic2"}
        response = staff_client.post("/api/clinics/", data)
        assert response.status_code == 403

    def test_staff_can_read_clinics(self, staff_client, clinic_a):
        response = staff_client.get("/api/clinics/")
        assert response.status_code == 200

    def test_admin_can_update_clinic(self, admin_client, clinic_a):
        response = admin_client.patch(f"/api/clinics/{clinic_a.pk}/", {"name": "Updated"})
        assert response.status_code == 200
        assert response.data["name"] == "Updated"

    def test_staff_cannot_update_clinic(self, staff_client, clinic_a):
        response = staff_client.patch(f"/api/clinics/{clinic_a.pk}/", {"name": "Hacked"})
        assert response.status_code == 403

    def test_admin_cannot_see_other_clinic(self, admin_client, clinic_b):
        """Admin de clínica A no puede ver clínica B en el listado."""
        response = admin_client.get("/api/clinics/")
        assert response.status_code == 200
        ids = [c["clinic_id"] for c in response.data["results"]]
        assert clinic_b.clinic_id not in ids

    def test_admin_cannot_retrieve_other_clinic(self, admin_client, clinic_b):
        """Admin de clínica A recibe 404 al intentar acceder al detalle de clínica B."""
        response = admin_client.get(f"/api/clinics/{clinic_b.clinic_id}/")
        assert response.status_code == 404

    def test_admin_cannot_update_other_clinic(self, admin_client, clinic_b):
        """Admin de clínica A recibe 404 al intentar modificar clínica B."""
        response = admin_client.patch(f"/api/clinics/{clinic_b.clinic_id}/", {"name": "Hackeada"})
        assert response.status_code == 404

    def test_superuser_sees_all_clinics(self, superuser_client, clinic_a, clinic_b):
        """Superusuario ve todas las clínicas."""
        response = superuser_client.get("/api/clinics/")
        assert response.status_code == 200
        ids = [c["clinic_id"] for c in response.data["results"]]
        assert clinic_a.clinic_id in ids
        assert clinic_b.clinic_id in ids

    def test_user_without_clinic_sees_none(self, api_client, db):
        """Usuario autenticado sin clínica asignada no ve ninguna clínica."""
        from core.models import User
        orphan = User.objects.create_user(
            email="orphan@test.com", password="pass", role=User.Role.ADMIN
        )
        api_client.force_authenticate(user=orphan)
        response = api_client.get("/api/clinics/")
        assert response.status_code == 200
        assert response.data["results"] == []


@pytest.mark.django_db
class TestUserViewSet:
    def test_list_users_scoped_to_clinic(self, admin_client, admin_user, staff_user, admin_user_b):
        response = admin_client.get("/api/users/")
        assert response.status_code == 200
        emails = [u["email"] for u in response.data["results"]]
        assert admin_user.email in emails
        assert staff_user.email in emails
        # admin_user_b is in clinic_b — must not appear
        assert admin_user_b.email not in emails

    def test_superuser_sees_all_users(self, superuser_client, admin_user, admin_user_b):
        response = superuser_client.get("/api/users/")
        assert response.status_code == 200
        emails = [u["email"] for u in response.data["results"]]
        assert admin_user.email in emails
        assert admin_user_b.email in emails

    def test_staff_can_read_users(self, staff_client, admin_user, staff_user):
        response = staff_client.get("/api/users/")
        assert response.status_code == 200

    def test_staff_cannot_create_user(self, staff_client, clinic_a):
        data = {
            "email": "new@test.com",
            "password": "pass123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = staff_client.post("/api/users/", data)
        assert response.status_code == 403

    def test_admin_can_create_user(self, admin_client, clinic_a):
        data = {
            "email": "newstaff@test.com",
            "password": "pass123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = admin_client.post("/api/users/", data)
        assert response.status_code == 201
        assert response.data["professional_id"] is not None

    def test_password_not_returned_in_response(self, admin_client, clinic_a):
        data = {
            "email": "nopasswd@test.com",
            "password": "secret123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = admin_client.post("/api/users/", data)
        assert "password" not in response.data


@pytest.mark.django_db
class TestPermissions:
    def test_unauthenticated_denied_on_all_safe_methods(self, api_client, clinic_a):
        for url in ["/api/clinics/", "/api/users/", "/api/patients/", "/api/services/", "/api/appointments/"]:
            response = api_client.get(url)
            assert response.status_code in (401, 403), f"Expected auth required for {url}"


@pytest.mark.django_db
class TestAgentConfigView:
    URL = "/api/clinics/{clinic_id}/agent-config/"
    EXPECTED_FIELDS = {
        "clinic_id", "name", "timezone",
        "whatsapp_phone_number_id", "whatsapp_token",
        "whatsapp_verify_token", "agent_api_key",
    }

    def _url(self, clinic_id):
        return self.URL.format(clinic_id=clinic_id)

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_get_with_correct_master_key_returns_200(self, api_client, clinic_a):
        response = api_client.get(
            self._url(clinic_a.clinic_id),
            HTTP_AUTHORIZATION=f"Api-Key {MASTER_KEY}",
        )
        assert response.status_code == 200

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_response_contains_expected_fields(self, api_client, clinic_a):
        response = api_client.get(
            self._url(clinic_a.clinic_id),
            HTTP_AUTHORIZATION=f"Api-Key {MASTER_KEY}",
        )
        assert self.EXPECTED_FIELDS == set(response.data.keys())

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_get_with_wrong_key_returns_403(self, api_client, clinic_a):
        response = api_client.get(
            self._url(clinic_a.clinic_id),
            HTTP_AUTHORIZATION="Api-Key wrong-key",
        )
        assert response.status_code == 403

    def test_get_without_authorization_header_returns_403(self, api_client, clinic_a):
        response = api_client.get(self._url(clinic_a.clinic_id))
        assert response.status_code == 403

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_nonexistent_clinic_returns_404(self, api_client):
        response = api_client.get(
            self._url("no-existe"),
            HTTP_AUTHORIZATION=f"Api-Key {MASTER_KEY}",
        )
        assert response.status_code == 404

    def test_agent_api_key_is_unique_per_clinic(self, clinic_a, clinic_b):
        assert clinic_a.agent_api_key != clinic_b.agent_api_key

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_whatsapp_token_not_exposed_in_clinic_viewset(self, admin_client, clinic_a):
        response = admin_client.get("/api/clinics/")
        assert response.status_code == 200
        for clinic_data in response.data["results"]:
            assert "whatsapp_token" not in clinic_data

    @override_settings(**AGENT_CONFIG_SETTINGS)
    def test_post_method_not_allowed(self, api_client, clinic_a):
        response = api_client.post(
            self._url(clinic_a.clinic_id),
            HTTP_AUTHORIZATION=f"Api-Key {MASTER_KEY}",
            data={},
        )
        assert response.status_code in (403, 405)
