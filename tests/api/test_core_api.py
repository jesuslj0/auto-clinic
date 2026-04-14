"""
API tests for core app: /api/clinics/, /api/users/, /api/auth/token/
"""
import pytest
from django.utils import timezone

from core.models import Clinic, User


@pytest.mark.django_db
class TestClinicViewSetList:
    def test_unauthenticated_returns_403(self, api_client):
        response = api_client.get("/api/clinics/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, clinic_a):
        response = admin_client.get("/api/clinics/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, clinic_a):
        response = staff_client.get("/api/clinics/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestClinicViewSetCreate:
    def test_admin_can_create(self, admin_client):
        data = {"clinic_id": "new-clinic-99", "name": "New Clinic"}
        response = admin_client.post("/api/clinics/", data)
        assert response.status_code == 201
        assert response.data["name"] == "New Clinic"

    def test_staff_cannot_create(self, staff_client):
        data = {"clinic_id": "staff-clinic-99", "name": "Staff Clinic"}
        response = staff_client.post("/api/clinics/", data)
        assert response.status_code == 403

    def test_create_requires_clinic_id_and_name(self, admin_client):
        response = admin_client.post("/api/clinics/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestClinicViewSetRetrieveUpdateDelete:
    def test_retrieve_clinic(self, admin_client, clinic_a):
        response = admin_client.get(f"/api/clinics/{clinic_a.pk}/")
        assert response.status_code == 200
        assert response.data["clinic_id"] == clinic_a.clinic_id

    def test_admin_can_update(self, admin_client, clinic_a):
        response = admin_client.patch(f"/api/clinics/{clinic_a.pk}/", {"name": "Updated Name"})
        assert response.status_code == 200
        assert response.data["name"] == "Updated Name"

    def test_staff_cannot_update(self, staff_client, clinic_a):
        response = staff_client.patch(f"/api/clinics/{clinic_a.pk}/", {"name": "Hacked"})
        assert response.status_code == 403

    def test_admin_can_delete(self, admin_client, db):
        clinic = Clinic.objects.create(clinic_id="to-delete", name="Delete Me")
        response = admin_client.delete(f"/api/clinics/{clinic.pk}/")
        assert response.status_code == 204

    def test_staff_cannot_delete(self, staff_client, clinic_a):
        response = staff_client.delete(f"/api/clinics/{clinic_a.pk}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestClinicViewSetExport:
    def test_export_returns_list(self, admin_client, clinic_a):
        response = admin_client.get("/api/clinics/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/clinics/export/")
        assert response.status_code in (401, 403)


@pytest.mark.django_db
class TestUserViewSetList:
    def test_unauthenticated_returns_403(self, api_client):
        response = api_client.get("/api/users/")
        assert response.status_code in (401, 403)

    def test_admin_sees_only_own_clinic_users(self, admin_client, admin_user, staff_user, admin_user_b):
        response = admin_client.get("/api/users/")
        assert response.status_code == 200
        emails = [u["email"] for u in response.data["results"]]
        assert admin_user.email in emails
        assert staff_user.email in emails
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


@pytest.mark.django_db
class TestUserViewSetCreate:
    def test_admin_can_create_user(self, admin_client, clinic_a):
        data = {
            "email": "created@test.com",
            "password": "testpass123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = admin_client.post("/api/users/", data)
        assert response.status_code == 201
        assert response.data["email"] == "created@test.com"
        assert response.data["professional_id"] is not None

    def test_staff_cannot_create_user(self, staff_client, clinic_a):
        data = {
            "email": "blocked@test.com",
            "password": "testpass123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = staff_client.post("/api/users/", data)
        assert response.status_code == 403

    def test_password_not_returned_in_response(self, admin_client, clinic_a):
        data = {
            "email": "nopwd@test.com",
            "password": "secret123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = admin_client.post("/api/users/", data)
        assert response.status_code == 201
        assert "password" not in response.data

    def test_user_creation_creates_professional_profile(self, admin_client, clinic_a):
        data = {
            "email": "profile@test.com",
            "password": "testpass123",
            "clinic": clinic_a.pk,
            "role": "staff",
        }
        response = admin_client.post("/api/users/", data)
        assert response.status_code == 201
        assert response.data["professional_id"] is not None


@pytest.mark.django_db
class TestUserViewSetRetrieveUpdateDelete:
    def test_retrieve_user(self, admin_client, admin_user):
        response = admin_client.get(f"/api/users/{admin_user.pk}/")
        assert response.status_code == 200
        assert response.data["email"] == admin_user.email

    def test_admin_can_update_user(self, admin_client, staff_user):
        response = admin_client.patch(f"/api/users/{staff_user.pk}/", {"first_name": "Updated"})
        assert response.status_code == 200
        assert response.data["first_name"] == "Updated"

    def test_staff_cannot_update_user(self, staff_client, admin_user):
        response = staff_client.patch(f"/api/users/{admin_user.pk}/", {"first_name": "Hacked"})
        assert response.status_code == 403

    def test_retrieve_other_clinic_user_not_found(self, admin_client, admin_user_b):
        # admin_user_b belongs to clinic_b; admin_client sees only clinic_a users
        response = admin_client.get(f"/api/users/{admin_user_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestUserViewSetExport:
    def test_export_returns_list(self, admin_client, admin_user):
        response = admin_client.get("/api/users/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)


@pytest.mark.django_db
class TestAuthTokenEndpoint:
    def test_valid_credentials_returns_token(self, db, admin_user):
        from rest_framework.test import APIClient
        client = APIClient()
        response = client.post("/api/auth/token/", {
            "username": admin_user.email,
            "password": "testpass123",
        })
        assert response.status_code == 200
        assert "token" in response.data

    def test_invalid_credentials_returns_400(self, db, admin_user):
        from rest_framework.test import APIClient
        client = APIClient()
        response = client.post("/api/auth/token/", {
            "username": admin_user.email,
            "password": "wrongpassword",
        })
        assert response.status_code == 400

    def test_missing_credentials_returns_400(self, db):
        from rest_framework.test import APIClient
        client = APIClient()
        response = client.post("/api/auth/token/", {})
        assert response.status_code == 400
