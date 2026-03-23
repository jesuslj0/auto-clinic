"""Tests for services app: models and API."""
import pytest

from services.models import Service


@pytest.mark.django_db
class TestServiceModel:
    def test_create_service(self, service_a):
        assert service_a.pk is not None
        assert service_a.name == "Consultation"
        assert service_a.is_active is True

    def test_str_representation(self, service_a):
        assert str(service_a) == "Consultation"

    def test_unique_together_clinic_name(self, db, clinic_a, service_a):
        with pytest.raises(Exception):
            Service.objects.create(
                clinic=clinic_a,
                name="Consultation",
                duration_minutes=45,
                price="60.00",
            )

    def test_same_name_different_clinic_allowed(self, db, clinic_a, clinic_b):
        Service.objects.create(
            clinic=clinic_a, name="Shared Service",
            duration_minutes=30, price="50.00",
        )
        s2 = Service.objects.create(
            clinic=clinic_b, name="Shared Service",
            duration_minutes=30, price="50.00",
        )
        assert s2.pk is not None


@pytest.mark.django_db
class TestServiceViewSet:
    def test_list_services_scoped_to_clinic(self, admin_client, service_a, service_b):
        response = admin_client.get("/api/services/")
        assert response.status_code == 200
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk in ids
        assert service_b.pk not in ids

    def test_staff_can_list_services(self, staff_client, service_a):
        response = staff_client.get("/api/services/")
        assert response.status_code == 200

    def test_staff_can_create_service(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "name": "New Service",
            "duration_minutes": 45,
            "price": "75.00",
        }
        response = staff_client.post("/api/services/", data)
        assert response.status_code == 201

    def test_other_clinic_service_not_visible(self, client_b, service_a):
        response = client_b.get("/api/services/")
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk not in ids

    def test_filter_by_is_active(self, admin_client, service_a, db, clinic_a):
        Service.objects.create(
            clinic=clinic_a, name="Inactive Service",
            duration_minutes=30, price="10.00", is_active=False,
        )
        response = admin_client.get("/api/services/?is_active=True")
        assert response.status_code == 200
        for s in response.data["results"]:
            assert s["is_active"] is True

    def test_retrieve_service_from_other_clinic_not_found(self, admin_client, service_b):
        response = admin_client.get(f"/api/services/{service_b.pk}/")
        assert response.status_code == 404

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/services/")
        assert response.status_code in (401, 403)
