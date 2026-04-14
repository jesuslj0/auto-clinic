"""Tests for core API endpoints: /api/clinics/ and /api/users/."""
import pytest
from django.urls import reverse

from core.models import Clinic, User


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
