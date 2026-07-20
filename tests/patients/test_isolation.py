"""Aislamiento multi-tenant de pacientes.

Cubre que una clínica no pueda ver NI escribir pacientes de otra, en los tres
frentes de acceso: API DRF (create / update / bulk-create), agente por Api-Key
y vistas HTML del panel (list / detail / edit).
"""
import pytest
from django.urls import reverse

from patients.models import Patient


@pytest.fixture
def agent_client(api_client, clinic_a):
    """APIClient con la Api-Key del agente de clinic_a."""
    api_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {clinic_a.agent_api_key}")
    return api_client


# ---------------------------------------------------------------------------
# API DRF — aislamiento de ESCRITURA
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatientApiWriteIsolation:
    def test_staff_cannot_create_patient_in_other_clinic(self, staff_client, clinic_a, clinic_b):
        """Aunque el payload traiga clinic_b, el paciente nace en la clínica del usuario."""
        data = {
            "clinic": clinic_b.pk,
            "first_name": "Mallory",
            "last_name": "Cross",
            "email": "mallory@test.com",
            "phone": "+34611000123",
        }
        response = staff_client.post("/api/patients/", data)
        assert response.status_code == 201
        patient = Patient.objects.get(pk=response.data["id"])
        assert patient.clinic_id == clinic_a.pk

    def test_staff_cannot_move_patient_to_other_clinic(self, staff_client, patient_a, clinic_a, clinic_b):
        """Un PATCH que intente reasignar la clínica se ignora."""
        response = staff_client.patch(
            f"/api/patients/{patient_a.pk}/",
            {"clinic": clinic_b.pk},
        )
        assert response.status_code == 200
        patient_a.refresh_from_db()
        assert patient_a.clinic_id == clinic_a.pk

    def test_bulk_create_forces_own_clinic(self, staff_client, clinic_a, clinic_b):
        payload = [
            {
                "clinic": clinic_b.pk,
                "first_name": f"Bulk{i}",
                "last_name": "Test",
                "email": f"bulk{i}@test.com",
                "phone": f"+3461100{i:04d}",
            }
            for i in range(3)
        ]
        response = staff_client.post("/api/patients/bulk-create/", payload, format="json")
        assert response.status_code == 201
        created = Patient.objects.filter(last_name="Test")
        assert created.count() == 3
        assert all(p.clinic_id == clinic_a.pk for p in created)

    def test_superuser_can_set_clinic_freely(self, superuser_client, clinic_b):
        """El superusuario sí gestiona cualquier clínica desde el payload."""
        data = {
            "clinic": clinic_b.pk,
            "first_name": "Root",
            "last_name": "Admin",
            "email": "root@test.com",
            "phone": "+34611009999",
        }
        response = superuser_client.post("/api/patients/", data)
        assert response.status_code == 201
        patient = Patient.objects.get(pk=response.data["id"])
        assert patient.clinic_id == clinic_b.pk


@pytest.mark.django_db
class TestPatientApiAgentIsolation:
    def test_agent_create_patient_scoped_to_its_clinic(self, agent_client, clinic_a, clinic_b):
        data = {
            "clinic": clinic_b.pk,
            "first_name": "Agent",
            "last_name": "Made",
            "email": "agentmade@test.com",
            "phone": "+34611007777",
        }
        response = agent_client.post("/api/patients/", data)
        assert response.status_code == 201
        patient = Patient.objects.get(pk=response.data["id"])
        assert patient.clinic_id == clinic_a.pk

    def test_agent_cannot_retrieve_other_clinic_patient(self, agent_client, patient_b):
        response = agent_client.get(f"/api/patients/{patient_b.pk}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Vistas HTML del panel — aislamiento de LECTURA/EDICIÓN
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatientHtmlViewsIsolation:
    def test_list_shows_only_own_clinic(self, client, admin_user, patient_a, patient_b):
        client.force_login(admin_user)
        response = client.get(reverse("patients:list"))
        assert response.status_code == 200
        patients = list(response.context["patients"])
        assert patient_a in patients
        assert patient_b not in patients

    def test_detail_other_clinic_is_404(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        response = client.get(reverse("patients:detail", kwargs={"id": patient_b.pk}))
        assert response.status_code == 404

    def test_edit_get_other_clinic_is_404(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        response = client.get(reverse("patients:edit", kwargs={"id": patient_b.pk}))
        assert response.status_code == 404

    def test_edit_post_other_clinic_is_404_and_unchanged(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        original_name = patient_b.first_name
        response = client.post(
            reverse("patients:edit", kwargs={"id": patient_b.pk}),
            {"first_name": "Hacked", "last_name": patient_b.last_name, "phone": patient_b.phone},
        )
        assert response.status_code == 404
        patient_b.refresh_from_db()
        assert patient_b.first_name == original_name


@pytest.mark.django_db
class TestPatientHtmlViewsRequireLogin:
    def test_list_requires_login(self, client):
        response = client.get(reverse("patients:list"))
        assert response.status_code == 302

    def test_detail_requires_login(self, client, patient_a):
        response = client.get(reverse("patients:detail", kwargs={"id": patient_a.pk}))
        assert response.status_code == 302

    def test_edit_requires_login(self, client, patient_a):
        response = client.get(reverse("patients:edit", kwargs={"id": patient_a.pk}))
        assert response.status_code == 302
