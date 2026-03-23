"""Tests for portal template views: appointment detail, confirm, cancel via token."""
import uuid

import pytest

from appointments.models import Appointment


@pytest.mark.django_db
class TestPortalDetailView:
    def test_detail_view_with_valid_token(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.get(f"/portal/{token}/")
        assert response.status_code == 200
        assert response.context["appointment"].pk == appointment_a.pk

    def test_detail_view_with_invalid_token_returns_404(self, client):
        fake_token = uuid.uuid4()
        response = client.get(f"/portal/{fake_token}/")
        assert response.status_code == 404

    def test_detail_context_contains_action_urls(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.get(f"/portal/{token}/")
        assert "portal_confirm_url" in response.context
        assert "portal_cancel_url" in response.context

    def test_detail_does_not_require_auth(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.get(f"/portal/{token}/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestPortalConfirmView:
    def test_confirm_updates_status(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.post(f"/portal/{token}/confirm/")
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CONFIRMED

    def test_confirm_with_invalid_token_returns_404(self, client):
        fake_token = uuid.uuid4()
        response = client.post(f"/portal/{fake_token}/confirm/")
        assert response.status_code == 404

    def test_confirm_does_not_require_auth(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.post(f"/portal/{token}/confirm/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestPortalCancelView:
    def test_cancel_updates_status(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.post(f"/portal/{token}/cancel/")
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CANCELLED

    def test_cancel_with_invalid_token_returns_404(self, client):
        fake_token = uuid.uuid4()
        response = client.post(f"/portal/{fake_token}/cancel/")
        assert response.status_code == 404

    def test_cancel_response_contains_message(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.post(f"/portal/{token}/cancel/")
        assert b"cancelled" in response.content.lower()

    def test_confirm_response_contains_message(self, client, appointment_a):
        token = appointment_a.confirmation_token
        response = client.post(f"/portal/{token}/confirm/")
        assert b"confirmed" in response.content.lower()
