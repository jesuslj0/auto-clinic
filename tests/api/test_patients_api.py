"""
API tests for patients app: /api/patients/
Covers CRUD, export, bulk-create, bulk-update, clinic isolation.
"""
import pytest

from patients.models import Patient


@pytest.mark.django_db
class TestPatientViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/patients/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, patient_a):
        response = admin_client.get("/api/patients/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, patient_a):
        response = staff_client.get("/api/patients/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, patient_a, patient_b):
        response = admin_client.get("/api/patients/")
        ids = [p["id"] for p in response.data["results"]]
        assert patient_a.pk in ids
        assert patient_b.pk not in ids

    def test_clinic_b_cannot_see_clinic_a_patients(self, client_b, patient_a):
        response = client_b.get("/api/patients/")
        ids = [p["id"] for p in response.data["results"]]
        assert patient_a.pk not in ids

    def test_superuser_sees_all(self, superuser_client, patient_a, patient_b):
        response = superuser_client.get("/api/patients/")
        ids = [p["id"] for p in response.data["results"]]
        assert patient_a.pk in ids
        assert patient_b.pk in ids


@pytest.mark.django_db
class TestPatientViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "first_name": "Alice",
            "last_name": "Wonderland",
            "email": "alice@example.com",
            "phone": "666-0001",
        }
        response = admin_client.post("/api/patients/", data)
        assert response.status_code == 201
        assert response.data["first_name"] == "Alice"

    def test_staff_can_create(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "first_name": "Bob",
            "last_name": "Builder",
            "email": "bob@example.com",
            "phone": "666-0002",
        }
        response = staff_client.post("/api/patients/", data)
        assert response.status_code == 201

    def test_create_requires_required_fields(self, admin_client):
        response = admin_client.post("/api/patients/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestPatientViewSetRetrieve:
    def test_retrieve_own_clinic_patient(self, admin_client, patient_a):
        response = admin_client.get(f"/api/patients/{patient_a.pk}/")
        assert response.status_code == 200
        assert response.data["id"] == patient_a.pk

    def test_retrieve_other_clinic_patient_returns_404(self, admin_client, patient_b):
        response = admin_client.get(f"/api/patients/{patient_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestPatientViewSetUpdate:
    def test_admin_can_update(self, admin_client, patient_a):
        response = admin_client.patch(f"/api/patients/{patient_a.pk}/", {"first_name": "Johnny"})
        assert response.status_code == 200
        assert response.data["first_name"] == "Johnny"

    def test_staff_can_update(self, staff_client, patient_a):
        response = staff_client.patch(f"/api/patients/{patient_a.pk}/", {"notes": "Updated note"})
        assert response.status_code == 200


@pytest.mark.django_db
class TestPatientViewSetDelete:
    def test_admin_can_delete(self, admin_client, clinic_a):
        p = Patient.objects.create(
            clinic=clinic_a, first_name="Del", last_name="Me",
            email="del@me.com", phone="000-9999",
        )
        response = admin_client.delete(f"/api/patients/{p.pk}/")
        assert response.status_code == 204

    def test_cannot_delete_other_clinic_patient(self, admin_client, patient_b):
        response = admin_client.delete(f"/api/patients/{patient_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestPatientViewSetExport:
    def test_export_returns_list(self, admin_client, patient_a):
        response = admin_client.get("/api/patients/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, patient_a, patient_b):
        response = admin_client.get("/api/patients/export/")
        ids = [p["id"] for p in response.data]
        assert patient_a.pk in ids
        assert patient_b.pk not in ids


@pytest.mark.django_db
class TestPatientBulkCreate:
    def test_bulk_create(self, admin_client, clinic_a):
        payload = [
            {
                "clinic": clinic_a.pk,
                "first_name": f"Patient{i}",
                "last_name": "Bulk",
                "email": f"bulk{i}@test.com",
                "phone": f"999-{i:04d}",
            }
            for i in range(3)
        ]
        response = admin_client.post("/api/patients/bulk-create/", payload, format="json")
        assert response.status_code == 201
        assert len(response.data) == 3

    def test_bulk_create_invalid_returns_400(self, admin_client):
        payload = [{"first_name": "NoClinic"}]
        response = admin_client.post("/api/patients/bulk-create/", payload, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestPatientBulkUpdate:
    def test_bulk_update(self, admin_client, patient_a):
        payload = [{"id": patient_a.pk, "notes": "bulk updated"}]
        response = admin_client.patch("/api/patients/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["updated"]) == 1
        assert response.data["updated"][0]["notes"] == "bulk updated"

    def test_bulk_update_nonexistent_id_reported_as_error(self, admin_client):
        payload = [{"id": 999999, "notes": "ghost"}]
        response = admin_client.patch("/api/patients/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["errors"]) == 1
