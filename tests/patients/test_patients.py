"""Tests for patients app: models and API."""
import pytest

from patients.models import Patient


@pytest.mark.django_db
class TestPatientModel:
    def test_create_patient(self, patient_a):
        assert patient_a.pk is not None
        assert patient_a.first_name == "John"
        assert patient_a.clinic is not None

    def test_str_representation(self, patient_a):
        assert str(patient_a) == "John Doe"

    def test_unique_together_constraint(self, db, clinic_a):
        Patient.objects.create(
            clinic=clinic_a,
            first_name="A",
            last_name="B",
            email="dup@test.com",
            phone="999",
        )
        with pytest.raises(Exception):
            Patient.objects.create(
                clinic=clinic_a,
                first_name="C",
                last_name="D",
                email="dup@test.com",
                phone="999",
            )

    def test_same_email_phone_different_clinic_allowed(self, db, clinic_a, clinic_b):
        Patient.objects.create(
            clinic=clinic_a, first_name="A", last_name="B",
            email="shared@test.com", phone="123",
        )
        p2 = Patient.objects.create(
            clinic=clinic_b, first_name="C", last_name="D",
            email="shared@test.com", phone="123",
        )
        assert p2.pk is not None


@pytest.mark.django_db
class TestPatientViewSet:
    def test_list_patients_scoped_to_clinic(self, admin_client, patient_a, patient_b):
        response = admin_client.get("/api/patients/")
        assert response.status_code == 200
        ids = [p["id"] for p in response.data["results"]]
        assert patient_a.pk in ids
        assert patient_b.pk not in ids

    def test_staff_can_list_patients(self, staff_client, patient_a):
        response = staff_client.get("/api/patients/")
        assert response.status_code == 200

    def test_staff_can_create_patient(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "first_name": "New",
            "last_name": "Patient",
            "email": "new.patient@test.com",
            "phone": "123456789",
        }
        response = staff_client.post("/api/patients/", data)
        assert response.status_code == 201

    def test_admin_b_cannot_see_clinic_a_patients(self, client_b, patient_a):
        response = client_b.get("/api/patients/")
        assert response.status_code == 200
        ids = [p["id"] for p in response.data["results"]]
        assert patient_a.pk not in ids

    def test_retrieve_patient_from_own_clinic(self, admin_client, patient_a):
        response = admin_client.get(f"/api/patients/{patient_a.pk}/")
        assert response.status_code == 200
        assert response.data["id"] == patient_a.pk

    def test_retrieve_patient_from_other_clinic_not_found(self, admin_client, patient_b):
        response = admin_client.get(f"/api/patients/{patient_b.pk}/")
        assert response.status_code == 404

    def test_search_by_name(self, admin_client, patient_a):
        response = admin_client.get("/api/patients/?search=John")
        assert response.status_code == 200
        assert any(p["first_name"] == "John" for p in response.data["results"])

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/patients/")
        assert response.status_code in (401, 403)
