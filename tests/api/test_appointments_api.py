"""
API tests for appointments app:
  /api/appointments/ (CRUD, export, bulk-create, bulk-update, status action)
  /api/public/appointments/<token>/confirm|cancel/
"""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from appointments.models import Appointment


@pytest.mark.django_db
class TestAppointmentViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/appointments/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, appointment_a):
        response = admin_client.get("/api/appointments/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, appointment_a):
        response = staff_client.get("/api/appointments/")
        assert response.status_code == 200

    def test_clinic_isolation_admin_a_cannot_see_clinic_b(self, admin_client, appointment_a, appointment_b):
        response = admin_client.get("/api/appointments/")
        ids = [str(a["id"]) for a in response.data["results"]]
        assert str(appointment_a.pk) in ids
        assert str(appointment_b.pk) not in ids

    def test_clinic_b_admin_cannot_see_clinic_a(self, client_b, appointment_a):
        response = client_b.get("/api/appointments/")
        assert response.status_code == 200
        ids = [str(a["id"]) for a in response.data["results"]]
        assert str(appointment_a.pk) not in ids

    def test_superuser_sees_all_clinics(self, superuser_client, appointment_a, appointment_b):
        response = superuser_client.get("/api/appointments/")
        assert response.status_code == 200
        ids = [str(a["id"]) for a in response.data["results"]]
        assert str(appointment_a.pk) in ids
        assert str(appointment_b.pk) in ids


@pytest.mark.django_db
class TestAppointmentViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a, patient_a, service_a, professional_a):
        now = timezone.now() + timedelta(hours=5)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional_a.pk,
            "scheduled_at": now.isoformat(),
            "end_at": (now + timedelta(minutes=30)).isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 201
        assert "confirmation_token" in response.data

    def test_staff_can_create(self, staff_client, clinic_a, patient_a, service_a, professional_a):
        now = timezone.now() + timedelta(hours=6)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional_a.pk,
            "scheduled_at": now.isoformat(),
        }
        response = staff_client.post("/api/appointments/", data)
        assert response.status_code == 201

    def test_create_requires_clinic_and_scheduled_at(self, admin_client):
        response = admin_client.post("/api/appointments/", {})
        assert response.status_code == 400

    def test_create_without_professional_autoassigns(
        self, admin_client, clinic_a, patient_a, service_a, professional_a
    ):
        """Sin `professional` en el payload, Django lo auto-asigna. Nunca queda None."""
        now = timezone.now() + timedelta(hours=5)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "scheduled_at": now.isoformat(),
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 201
        assert response.data["professional"] == professional_a.pk

    def test_confirmation_token_auto_generated(
        self, admin_client, clinic_a, patient_a, service_a, professional_a
    ):
        now = timezone.now() + timedelta(hours=7)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional_a.pk,
            "scheduled_at": now.isoformat(),
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 201
        assert response.data["confirmation_token"] is not None


@pytest.mark.django_db
class TestAppointmentViewSetRetrieve:
    def test_retrieve_own_clinic_appointment(self, admin_client, appointment_a):
        response = admin_client.get(f"/api/appointments/{appointment_a.pk}/")
        assert response.status_code == 200
        assert str(response.data["id"]) == str(appointment_a.pk)

    def test_retrieve_other_clinic_appointment_returns_404(self, admin_client, appointment_b):
        response = admin_client.get(f"/api/appointments/{appointment_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestAppointmentViewSetUpdate:
    def test_patch_cannot_confirm(self, admin_client, appointment_a):
        """Confirmar no se hace escribiendo `status` por la API: es del staff/panel."""
        response = admin_client.patch(
            f"/api/appointments/{appointment_a.pk}/",
            {"status": "confirmed"},
        )
        assert response.status_code == 400
        appointment_a.refresh_from_db()
        assert appointment_a.status != "confirmed"

    def test_staff_confirm_endpoint_puts_it_in_firm(self, admin_client, appointment_a):
        """La vía legítima: el endpoint dedicado del staff."""
        response = admin_client.post(f"/api/appointments/{appointment_a.pk}/confirm/")
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == "confirmed"

    def test_confirmation_token_is_readonly(self, admin_client, appointment_a):
        original = str(appointment_a.confirmation_token)
        response = admin_client.patch(
            f"/api/appointments/{appointment_a.pk}/",
            {"confirmation_token": str(uuid.uuid4())},
        )
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert str(appointment_a.confirmation_token) == original

    def test_filter_by_status(self, admin_client, appointment_a):
        response = admin_client.get("/api/appointments/?status=pending")
        assert response.status_code == 200
        for item in response.data["results"]:
            assert item["status"] == "pending"


@pytest.mark.django_db
class TestAppointmentViewSetDelete:
    def test_admin_can_delete(self, admin_client, appointment_a):
        response = admin_client.delete(f"/api/appointments/{appointment_a.pk}/")
        assert response.status_code == 204
        assert not Appointment.objects.filter(pk=appointment_a.pk).exists()

    def test_cannot_delete_other_clinic_appointment(self, admin_client, appointment_b):
        response = admin_client.delete(f"/api/appointments/{appointment_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestAppointmentViewSetExport:
    def test_export_returns_unpaginated_list(self, admin_client, appointment_a):
        response = admin_client.get("/api/appointments/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)
        ids = [str(a["id"]) for a in response.data]
        assert str(appointment_a.pk) in ids

    def test_export_respects_clinic_isolation(self, admin_client, appointment_a, appointment_b):
        response = admin_client.get("/api/appointments/export/")
        ids = [str(a["id"]) for a in response.data]
        assert str(appointment_a.pk) in ids
        assert str(appointment_b.pk) not in ids


@pytest.mark.django_db
class TestAppointmentBulkCreate:
    def test_bulk_create_appointments(
        self, admin_client, clinic_a, patient_a, service_a, professional_a
    ):
        now = timezone.now() + timedelta(hours=10)
        payload = [
            {
                "clinic": clinic_a.pk,
                "patient": patient_a.pk,
                "service": service_a.pk,
                "professional": professional_a.pk,
                "scheduled_at": (now + timedelta(hours=i)).isoformat(),
            }
            for i in range(3)
        ]
        response = admin_client.post("/api/appointments/bulk-create/", payload, format="json")
        assert response.status_code == 201
        assert len(response.data) == 3

    def test_bulk_create_invalid_payload_returns_400(self, admin_client):
        payload = [{"status": "pending"}]  # missing required clinic and scheduled_at
        response = admin_client.post("/api/appointments/bulk-create/", payload, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestAppointmentBulkUpdate:
    def test_bulk_update_appointments(self, admin_client, appointment_a):
        # No se confirma escribiendo `status` por la API (eso tiene su propia vía
        # con guardia); un campo que no toca el hueco, como `notes`, sí se edita.
        payload = [{"id": str(appointment_a.pk), "notes": "actualizado en lote"}]
        response = admin_client.patch("/api/appointments/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["updated"]) == 1
        assert response.data["updated"][0]["notes"] == "actualizado en lote"

    def test_bulk_update_with_invalid_id_reports_error(self, admin_client):
        payload = [{"id": str(uuid.uuid4()), "status": "confirmed"}]
        response = admin_client.patch("/api/appointments/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["errors"]) == 1


@pytest.mark.django_db
class TestAppointmentStatusAction:
    def test_status_action_returns_info(self, admin_client, appointment_a):
        response = admin_client.get(f"/api/appointments/{appointment_a.pk}/status/")
        assert response.status_code == 200
        assert response.data["id"] == str(appointment_a.pk)
        assert "status" in response.data
        assert "confirmation_token" in response.data
        assert "scheduled_at" in response.data

    def test_status_action_other_clinic_returns_404(self, admin_client, appointment_b):
        response = admin_client.get(f"/api/appointments/{appointment_b.pk}/status/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestPublicAppointmentTokenActions:
    def test_confirm_without_auth(self, api_client, appointment_a):
        """El "SÍ" del paciente se registra como hecho; el estado NO cambia.

        Confirmar (poner en firme) es de la clínica. El paciente solo dice si
        piensa venir, y eso ni ocupa ni libera el hueco.
        """
        estado_previo = appointment_a.status
        token = appointment_a.confirmation_token
        response = api_client.post(f"/api/public/appointments/{token}/confirm/")
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == estado_previo
        assert appointment_a.patient_confirmed_at is not None

    def test_cancel_without_auth(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        response = api_client.post(f"/api/public/appointments/{token}/cancel/")
        assert response.status_code == 200
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CANCELLED

    def test_invalid_token_returns_404(self, api_client):
        response = api_client.post(f"/api/public/appointments/{uuid.uuid4()}/confirm/")
        assert response.status_code == 404

    def test_unsupported_action_returns_400(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        response = api_client.post(f"/api/public/appointments/{token}/reschedule/")
        assert response.status_code == 400

    def test_confirm_response_contains_appointment_data(self, api_client, appointment_a):
        token = appointment_a.confirmation_token
        response = api_client.post(f"/api/public/appointments/{token}/confirm/")
        assert response.status_code == 200
        assert "id" in response.data
        assert response.data["status"] == appointment_a.status


@pytest.mark.django_db
class TestAppointmentWriteIsolation:
    """Un usuario de la clínica A no puede crear citas en la clínica B mandando
    otro `clinic` en el payload: la clínica se fija a la del usuario ANTES de la
    comprobación servicio↔clínica."""

    def test_create_ignores_payload_clinic_and_forces_own(
        self, admin_client, clinic_a, clinic_b, patient_a, service_a, professional_a
    ):
        now = timezone.now() + timedelta(hours=5)
        data = {
            "clinic": clinic_b.pk,  # intento de crear en otra clínica
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional_a.pk,
            "scheduled_at": now.isoformat(),
            "end_at": (now + timedelta(minutes=30)).isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 201
        appointment = Appointment.objects.get(pk=response.data["id"])
        assert appointment.clinic_id == clinic_a.pk

    def test_create_in_other_clinic_with_its_resources_is_rejected(
        self, admin_client, clinic_b, patient_b, service_b
    ):
        """Con `clinic`, paciente y servicio TODOS de la clínica B, el enforce fija
        la clínica a la A del usuario y la comprobación servicio↔clínica falla."""
        now = timezone.now() + timedelta(hours=5)
        data = {
            "clinic": clinic_b.pk,
            "patient": patient_b.pk,
            "service": service_b.pk,
            "scheduled_at": now.isoformat(),
            "end_at": (now + timedelta(minutes=60)).isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data)
        assert response.status_code == 400
