"""
API tests for services app: /api/services/
Covers CRUD, export, clinic isolation.
"""
import pytest

from services.models import Service


@pytest.mark.django_db
class TestServiceViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/services/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, service_a):
        response = admin_client.get("/api/services/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, service_a):
        response = staff_client.get("/api/services/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, service_a, service_b):
        response = admin_client.get("/api/services/")
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk in ids
        assert service_b.pk not in ids

    def test_clinic_b_cannot_see_clinic_a(self, client_b, service_a):
        response = client_b.get("/api/services/")
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk not in ids

    def test_superuser_sees_all(self, superuser_client, service_a, service_b):
        response = superuser_client.get("/api/services/")
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk in ids
        assert service_b.pk in ids


@pytest.mark.django_db
class TestServiceViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "name": "New Service",
            "duration_minutes": 45,
            "price": "75.00",
            "is_active": True,
        }
        response = admin_client.post("/api/services/", data)
        assert response.status_code == 201
        assert response.data["name"] == "New Service"

    def test_staff_can_create(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "name": "Staff Service",
            "duration_minutes": 30,
            "price": "40.00",
        }
        response = staff_client.post("/api/services/", data)
        assert response.status_code == 201

    def test_create_requires_fields(self, admin_client):
        response = admin_client.post("/api/services/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestServiceViewSetRetrieve:
    def test_retrieve_own_clinic_service(self, admin_client, service_a):
        response = admin_client.get(f"/api/services/{service_a.pk}/")
        assert response.status_code == 200
        assert response.data["id"] == service_a.pk

    def test_retrieve_other_clinic_service_returns_404(self, admin_client, service_b):
        response = admin_client.get(f"/api/services/{service_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestServiceViewSetUpdate:
    def test_admin_can_update(self, admin_client, service_a):
        response = admin_client.patch(f"/api/services/{service_a.pk}/", {"price": "99.00"})
        assert response.status_code == 200
        assert response.data["price"] == "99.00"

    def test_staff_can_update(self, staff_client, service_a):
        response = staff_client.patch(f"/api/services/{service_a.pk}/", {"duration_minutes": 60})
        assert response.status_code == 200


@pytest.mark.django_db
class TestServiceViewSetDelete:
    def test_admin_can_delete(self, admin_client, clinic_a):
        svc = Service.objects.create(
            clinic=clinic_a, name="To Delete", duration_minutes=15, price="10.00"
        )
        response = admin_client.delete(f"/api/services/{svc.pk}/")
        assert response.status_code == 204

    def test_cannot_delete_other_clinic_service(self, admin_client, service_b):
        response = admin_client.delete(f"/api/services/{service_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestServiceViewSetExport:
    def test_export_returns_list(self, admin_client, service_a):
        response = admin_client.get("/api/services/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, service_a, service_b):
        response = admin_client.get("/api/services/export/")
        ids = [s["id"] for s in response.data]
        assert service_a.pk in ids
        assert service_b.pk not in ids
