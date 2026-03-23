"""Tests for booking template views (no models, public access)."""
import pytest


@pytest.mark.django_db
class TestBookingViews:
    def test_service_list_view_ok(self, client, service_a):
        response = client.get("/booking/")
        assert response.status_code == 200

    def test_service_list_shows_active_services(self, client, service_a, clinic_a):
        from services.models import Service
        Service.objects.create(
            clinic=clinic_a, name="Hidden Service",
            duration_minutes=30, price="20.00", is_active=False,
        )
        response = client.get("/booking/")
        services = response.context["services"]
        names = [s.name for s in services]
        assert "Consultation" in names
        assert "Hidden Service" not in names

    def test_datetime_view_ok(self, client, service_a):
        response = client.get(f"/booking/datetime/?service={service_a.pk}")
        assert response.status_code == 200
        assert "available_slots" in response.context

    def test_datetime_view_with_no_service(self, client):
        response = client.get("/booking/datetime/")
        assert response.status_code == 200

    def test_confirm_view_get_ok(self, client, service_a):
        response = client.get(f"/booking/confirm/?service={service_a.pk}")
        assert response.status_code == 200

    def test_confirm_view_post_redirects_to_success(self, client):
        response = client.post("/booking/confirm/")
        assert response.status_code == 302
        assert "/booking/success/" in response["Location"]

    def test_success_view_ok(self, client):
        response = client.get("/booking/success/")
        assert response.status_code == 200

    def test_context_has_service(self, client, service_a):
        response = client.get(f"/booking/datetime/?service={service_a.pk}")
        assert response.context["service"].pk == service_a.pk

    def test_available_slots_are_non_empty(self, client, service_a):
        response = client.get(f"/booking/datetime/?service={service_a.pk}")
        assert len(response.context["available_slots"]) > 0
