"""Tests for appointment API: CRUD, filtering, clinic isolation, token endpoints."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from appointments.models import Appointment


@pytest.mark.django_db
class TestAppointmentViewSet:
    def test_list_appointments_scoped_to_clinic(self, admin_client, appointment_a, appointment_b):
        response = admin_client.get("/api/appointments/")
        assert response.status_code == 200
        ids = [a["id"] for a in response.data["results"]]
        assert str(appointment_a.pk) in ids
        assert str(appointment_b.pk) not in ids

    def test_staff_can_list_appointments(self, staff_client, appointment_a):
        response = staff_client.get("/api/appointments/")
        assert response.status_code == 200

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/appointments/")
        assert response.status_code in (401, 403)

    def test_superuser_sees_all_appointments(self, superuser_client, appointment_a, appointment_b):
        response = superuser_client.get("/api/appointments/")
        assert response.status_code == 200
        ids = [a["id"] for a in response.data["results"]]
        assert str(appointment_a.pk) in ids
        assert str(appointment_b.pk) in ids

    def test_admin_b_cannot_see_clinic_a_appointments(self, client_b, appointment_a):
        response = client_b.get("/api/appointments/")
        ids = [a["id"] for a in response.data["results"]]
        assert appointment_a.pk not in ids

    def test_create_appointment(self, admin_client, clinic_a, patient_a, service_a):
        now = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "scheduled_at": now.isoformat(),
            "end_at": (now + timedelta(minutes=30)).isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 201
        assert "confirmation_token" in response.data

    def test_staff_can_create_appointment(self, staff_client, clinic_a, patient_a, service_a):
        now = timezone.now() + timedelta(hours=4)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "scheduled_at": now.isoformat(),
            "end_at": (now + timedelta(minutes=30)).isoformat(),
        }
        response = staff_client.post("/api/appointments/", data)
        assert response.status_code == 201

    def test_retrieve_appointment_from_other_clinic_not_found(self, admin_client, appointment_b):
        response = admin_client.get(f"/api/appointments/{appointment_b.pk}/")
        assert response.status_code == 404

    def test_update_status(self, admin_client, appointment_a):
        response = admin_client.patch(
            f"/api/appointments/{appointment_a.pk}/",
            {"status": "confirmed"},
        )
        assert response.status_code == 200
        assert response.data["status"] == "confirmed"

    def test_filter_by_status(self, admin_client, appointment_a):
        response = admin_client.get("/api/appointments/?status=pending")
        assert response.status_code == 200
        for appt in response.data["results"]:
            assert appt["status"] == "pending"

    def test_confirmation_token_readonly(self, admin_client, appointment_a):
        original_token = str(appointment_a.confirmation_token)
        fake_token = str(uuid.uuid4())
        response = admin_client.patch(
            f"/api/appointments/{appointment_a.pk}/",
            {"confirmation_token": fake_token},
        )
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert str(appointment_a.confirmation_token) == original_token


@pytest.mark.django_db
class TestAppointmentTokenActions:
    def test_confirm_via_token(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        url = f"/api/public/appointments/{token}/confirm/"
        response = api_client.post(url)
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CONFIRMED

    def test_cancel_via_token(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        url = f"/api/public/appointments/{token}/cancel/"
        response = api_client.post(url)
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CANCELLED

    def test_invalid_token_returns_404(self, api_client):
        fake_token = uuid.uuid4()
        url = f"/api/public/appointments/{fake_token}/confirm/"
        response = api_client.post(url)
        assert response.status_code == 404

    def test_unsupported_action_returns_400(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        url = f"/api/public/appointments/{token}/reschedule/"
        response = api_client.post(url)
        assert response.status_code == 400

    def test_token_action_does_not_require_auth(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        url = f"/api/public/appointments/{token}/confirm/"
        # No authentication set on api_client
        response = api_client.post(url)
        assert response.status_code == 200

    def test_response_contains_appointment_data(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        url = f"/api/public/appointments/{token}/confirm/"
        response = api_client.post(url)
        assert "id" in response.data
        assert "status" in response.data
        assert response.data["status"] == "confirmed"
