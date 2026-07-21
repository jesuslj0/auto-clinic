"""
API tests for /api/professionals/ (ProfessionalViewSet).

Se centran en el aislamiento multitenant de ESCRITURA. Los profesionales se
auto-crean por señal al guardar un User con rol y clínica
(`ensure_professional_for_user`), así que el flujo de escritura relevante por API
es el UPDATE: mover un profesional a otra clínica vía payload debe ignorarse.
"""
import pytest


@pytest.mark.django_db
class TestProfessionalReadIsolation:
    def test_admin_a_cannot_see_clinic_b_professional(self, admin_client, professional_a, admin_user_b):
        other = admin_user_b.professional_profile
        response = admin_client.get("/api/professionals/")
        ids = [p["id"] for p in response.data["results"]]
        assert professional_a.pk in ids
        assert other.pk not in ids

    def test_retrieve_other_clinic_professional_returns_404(self, admin_client, admin_user_b):
        other = admin_user_b.professional_profile
        response = admin_client.get(f"/api/professionals/{other.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestProfessionalWriteIsolation:
    """Un usuario de la clínica A no puede mover un profesional a la clínica B
    mandando otro `clinic` en el payload. Solo el superusuario puede."""

    def test_update_cannot_move_to_other_clinic(self, admin_client, clinic_b, professional_a):
        response = admin_client.patch(
            f"/api/professionals/{professional_a.pk}/", {"clinic": clinic_b.pk}
        )
        assert response.status_code == 200
        professional_a.refresh_from_db()
        assert professional_a.clinic_id != clinic_b.pk

    def test_update_other_clinic_service_rejected(self, admin_client, professional_a, service_b):
        """Asignar un servicio de otra clínica falla: la clínica queda fijada a la
        del usuario y la comprobación servicio↔clínica lo rechaza."""
        response = admin_client.patch(
            f"/api/professionals/{professional_a.pk}/",
            {"service_ids": [service_b.pk]},
            format="json",
        )
        assert response.status_code == 400

    def test_superuser_can_move_professional(self, superuser_client, clinic_b, professional_a):
        response = superuser_client.patch(
            f"/api/professionals/{professional_a.pk}/", {"clinic": clinic_b.pk}
        )
        assert response.status_code == 200
        professional_a.refresh_from_db()
        assert professional_a.clinic_id == clinic_b.pk
