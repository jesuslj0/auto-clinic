"""
API tests for notifications app: /api/reminders/
Covers CRUD, export, clinic isolation.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from notifications.models import Reminder


@pytest.mark.django_db
class TestReminderViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/reminders/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, reminder_a):
        response = admin_client.get("/api/reminders/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, reminder_a):
        response = staff_client.get("/api/reminders/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, reminder_a, clinic_b, appointment_b):
        reminder_b = Reminder.objects.create(
            clinic=clinic_b,
            appointment=appointment_b,
            reminder_type=Reminder.ReminderType.H24,
            scheduled_for=appointment_b.scheduled_at - timedelta(hours=24),
        )
        response = admin_client.get("/api/reminders/")
        ids = [r["id"] for r in response.data["results"]]
        assert reminder_a.pk in ids
        assert reminder_b.pk not in ids

    def test_clinic_b_cannot_see_clinic_a_reminders(self, client_b, reminder_a):
        response = client_b.get("/api/reminders/")
        ids = [r["id"] for r in response.data["results"]]
        assert reminder_a.pk not in ids

    def test_superuser_sees_all(self, superuser_client, reminder_a, clinic_b, appointment_b):
        reminder_b = Reminder.objects.create(
            clinic=clinic_b,
            appointment=appointment_b,
            reminder_type=Reminder.ReminderType.H2,
            scheduled_for=appointment_b.scheduled_at - timedelta(hours=2),
        )
        response = superuser_client.get("/api/reminders/")
        ids = [r["id"] for r in response.data["results"]]
        assert reminder_a.pk in ids
        assert reminder_b.pk in ids


@pytest.mark.django_db
class TestReminderViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a, appointment_a):
        data = {
            "clinic": clinic_a.pk,
            "appointment": str(appointment_a.pk),
            "reminder_type": "2h",
            "scheduled_for": (appointment_a.scheduled_at - timedelta(hours=2)).isoformat(),
        }
        response = admin_client.post("/api/reminders/", data)
        assert response.status_code == 201

    def test_staff_can_create(self, staff_client, clinic_a, appointment_a):
        # appointment_a already has a 24h reminder (reminder_a fixture); use 2h
        data = {
            "clinic": clinic_a.pk,
            "appointment": str(appointment_a.pk),
            "reminder_type": "2h",
            "scheduled_for": (appointment_a.scheduled_at - timedelta(hours=2)).isoformat(),
        }
        response = staff_client.post("/api/reminders/", data)
        assert response.status_code == 201

    def test_create_requires_required_fields(self, admin_client):
        response = admin_client.post("/api/reminders/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestReminderViewSetRetrieve:
    def test_retrieve_own_clinic_reminder(self, admin_client, reminder_a):
        response = admin_client.get(f"/api/reminders/{reminder_a.pk}/")
        assert response.status_code == 200
        assert response.data["id"] == reminder_a.pk

    def test_retrieve_other_clinic_reminder_returns_404(self, admin_client, clinic_b, appointment_b):
        reminder_b = Reminder.objects.create(
            clinic=clinic_b,
            appointment=appointment_b,
            reminder_type=Reminder.ReminderType.H24,
            scheduled_for=appointment_b.scheduled_at - timedelta(hours=24),
        )
        response = admin_client.get(f"/api/reminders/{reminder_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestReminderViewSetUpdate:
    def test_can_update_reminder(self, admin_client, reminder_a):
        response = admin_client.patch(f"/api/reminders/{reminder_a.pk}/", {"success": True})
        assert response.status_code == 200
        assert response.data["success"] is True


@pytest.mark.django_db
class TestReminderViewSetDelete:
    def test_can_delete_reminder(self, admin_client, reminder_a):
        pk = reminder_a.pk
        response = admin_client.delete(f"/api/reminders/{reminder_a.pk}/")
        assert response.status_code == 204
        assert not Reminder.objects.filter(pk=pk).exists()


@pytest.mark.django_db
class TestReminderViewSetExport:
    def test_export_returns_list(self, admin_client, reminder_a):
        response = admin_client.get("/api/reminders/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, reminder_a, clinic_b, appointment_b):
        reminder_b = Reminder.objects.create(
            clinic=clinic_b,
            appointment=appointment_b,
            reminder_type=Reminder.ReminderType.H2,
            scheduled_for=appointment_b.scheduled_at - timedelta(hours=2),
        )
        response = admin_client.get("/api/reminders/export/")
        ids = [r["id"] for r in response.data]
        assert reminder_a.pk in ids
        assert reminder_b.pk not in ids
