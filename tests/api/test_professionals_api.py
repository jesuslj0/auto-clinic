"""
API tests for /api/professionals/ (ProfessionalViewSet).

Se centran en el aislamiento multitenant de ESCRITURA. Los profesionales se
auto-crean por señal al guardar un User con rol y clínica
(`ensure_professional_for_user`), así que el flujo de escritura relevante por API
es el UPDATE: mover un profesional a otra clínica vía payload debe ignorarse.
"""
import pytest

from appointments.models import Professional


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


@pytest.mark.django_db
class TestProfessionalIdentityFields:
    """El agente de WhatsApp se presenta con estos campos: tratamiento,
    presentación y horario semanal. Si el serializer deja de exponerlos, el bot
    vuelve a contestar «Elena Garrido (Podólogo)» sin horario."""

    def test_list_exposes_title_bio_and_schedules(self, admin_client, professional_a):
        professional_a.title = Professional.Title.DRA
        professional_a.bio = "Podóloga colegiada, especializada en biomecánica."
        professional_a.professional_type = Professional.ProfessionalType.PODOLOGO
        professional_a.save()

        response = admin_client.get(f"/api/professionals/{professional_a.pk}/")

        assert response.status_code == 200
        data = response.data
        assert data["title"] == "dra"
        assert data["title_display"] == "Dra."
        assert data["display_name"].startswith("Dra. ")
        assert data["bio"] == "Podóloga colegiada, especializada en biomecánica."
        # La etiqueta del choice no cambia: es la que ven panel y filtros.
        assert data["professional_type_display"] == "Podólogo"
        assert data["professional_type_label"] == "Podóloga"
        assert len(data["schedules"]) == 7
        assert data["schedules"][0]["day_of_week_display"] == "Lunes"

    def test_title_and_bio_are_read_only(self, admin_client, professional_a):
        """Se leen, no se escriben: el token de n8n llega a este endpoint con
        PATCH, y la ficha del profesional se edita desde el panel."""
        response = admin_client.patch(
            f"/api/professionals/{professional_a.pk}/", {"title": "dr", "bio": "Hola."}
        )
        assert response.status_code == 200
        professional_a.refresh_from_db()
        assert professional_a.title == ""
        assert professional_a.bio == ""

    def test_licence_is_not_exposed(self, admin_client, professional_a):
        """La colegiación sale en informes y facturas, no por la API del agente."""
        response = admin_client.get(f"/api/professionals/{professional_a.pk}/")
        assert "license_number" not in response.data
        assert "license_body" not in response.data


@pytest.mark.django_db
class TestProfessionalTypeLabel:
    """El género sale del tratamiento y de nada más: no hay campo de sexo en la
    ficha y no se infiere del nombre."""

    @pytest.mark.parametrize(
        "title,expected",
        [
            (Professional.Title.DRA, "Podóloga"),
            (Professional.Title.DNA, "Podóloga"),
            (Professional.Title.DR, "Podólogo"),
            (Professional.Title.D, "Podólogo"),
            ("", "Podólogo"),
        ],
    )
    def test_gendered_label(self, professional_a, title, expected):
        professional_a.professional_type = Professional.ProfessionalType.PODOLOGO
        professional_a.title = title
        assert professional_a.professional_type_label == expected

    def test_invariable_type_keeps_choice_label(self, professional_a):
        """Un tipo sin forma femenina propia usa su etiqueta tal cual."""
        professional_a.professional_type = Professional.ProfessionalType.FISIOTERAPEUTA
        professional_a.title = Professional.Title.DRA
        assert professional_a.professional_type_label == "Fisioterapeuta"

    def test_enfermero_drops_the_slashed_label(self, professional_a):
        """La etiqueta del choice es «Enfermero/a»; concordada nunca lleva barra."""
        professional_a.professional_type = Professional.ProfessionalType.ENFERMERO
        professional_a.title = Professional.Title.DNA
        assert professional_a.professional_type_label == "Enfermera"
        professional_a.title = Professional.Title.DR
        assert professional_a.professional_type_label == "Enfermero"
