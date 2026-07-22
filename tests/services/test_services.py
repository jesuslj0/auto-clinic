"""Tests for services app: models and API."""
import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from services.models import Service


@pytest.mark.django_db
class TestServiceModel:
    def test_create_service(self, service_a):
        assert service_a.pk is not None
        assert service_a.name == "Consultation"
        assert service_a.is_active is True

    def test_str_representation(self, service_a):
        assert str(service_a) == "Consultation"

    def test_unique_together_clinic_name(self, db, clinic_a, service_a):
        with pytest.raises(Exception):
            Service.objects.create(
                clinic=clinic_a,
                name="Consultation",
                duration_minutes=45,
                price="60.00",
            )

    def test_same_name_different_clinic_allowed(self, db, clinic_a, clinic_b):
        Service.objects.create(
            clinic=clinic_a, name="Shared Service",
            duration_minutes=30, price="50.00",
        )
        s2 = Service.objects.create(
            clinic=clinic_b, name="Shared Service",
            duration_minutes=30, price="50.00",
        )
        assert s2.pk is not None


@pytest.mark.django_db
class TestServiceViewSet:
    def test_list_services_scoped_to_clinic(self, admin_client, service_a, service_b):
        response = admin_client.get("/api/services/")
        assert response.status_code == 200
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk in ids
        assert service_b.pk not in ids

    def test_staff_can_list_services(self, staff_client, service_a):
        response = staff_client.get("/api/services/")
        assert response.status_code == 200

    def test_staff_can_create_service(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "name": "New Service",
            "duration_minutes": 45,
            "price": "75.00",
        }
        response = staff_client.post("/api/services/", data)
        assert response.status_code == 201

    def test_other_clinic_service_not_visible(self, client_b, service_a):
        response = client_b.get("/api/services/")
        ids = [s["id"] for s in response.data["results"]]
        assert service_a.pk not in ids

    def test_filter_by_is_active(self, admin_client, service_a, db, clinic_a):
        Service.objects.create(
            clinic=clinic_a, name="Inactive Service",
            duration_minutes=30, price="10.00", is_active=False,
        )
        response = admin_client.get("/api/services/?is_active=True")
        assert response.status_code == 200
        for s in response.data["results"]:
            assert s["is_active"] is True

    def test_retrieve_service_from_other_clinic_not_found(self, admin_client, service_b):
        response = admin_client.get(f"/api/services/{service_b.pk}/")
        assert response.status_code == 404

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/services/")
        assert response.status_code in (401, 403)


@pytest.mark.django_db
class TestServiceVariableRanges:
    """Precio y duración variables: qué se ocupa en agenda y cómo se lee."""

    def _variable(self, clinic, **kwargs):
        datos = dict(
            clinic=clinic,
            name="Limpieza",
            duration_minutes=30,
            duration_type=Service.ValueType.VARIABLE,
            duration_max_minutes=60,
            price="40.00",
            price_type=Service.ValueType.VARIABLE,
            price_max="80.00",
        )
        datos.update(kwargs)
        return Service(**datos)

    def test_booking_duration_es_el_maximo_si_es_variable(self, clinic_a):
        assert self._variable(clinic_a).booking_duration_minutes == 60

    def test_booking_duration_es_el_minimo_sin_maximo(self, clinic_a):
        servicio = self._variable(clinic_a, duration_max_minutes=None)
        assert servicio.booking_duration_minutes == 30

    def test_booking_duration_de_un_servicio_fijo(self, service_a):
        assert service_a.booking_duration_minutes == service_a.duration_minutes

    def test_display_de_un_servicio_fijo(self, service_a):
        assert service_a.duration_display == "30 min"
        assert service_a.price_display == "50 €"

    def test_display_de_un_rango(self, clinic_a):
        servicio = self._variable(clinic_a)
        assert servicio.duration_display == "30 – 60 min"
        assert servicio.price_display == "40 – 80 €"

    def test_display_sin_maximo_es_desde(self, clinic_a):
        servicio = self._variable(clinic_a, duration_max_minutes=None, price_max=None)
        assert servicio.duration_display == "Desde 30 min"
        assert servicio.price_display == "Desde 40 €"

    def test_el_maximo_debe_superar_al_minimo(self, clinic_a):
        servicio = self._variable(clinic_a, duration_max_minutes=30, price_max="40.00")

        with pytest.raises(DjangoValidationError) as exc:
            servicio.clean()

        assert set(exc.value.message_dict) == {"duration_max_minutes", "price_max"}

    def test_un_valor_fijo_descarta_el_maximo(self, clinic_a):
        """Cambiar de variable a fijo no puede dejar el máximo antiguo colgando."""
        servicio = self._variable(
            clinic_a,
            duration_type=Service.ValueType.FIXED,
            price_type=Service.ValueType.FIXED,
        )

        servicio.clean()

        assert servicio.duration_max_minutes is None
        assert servicio.price_max is None
