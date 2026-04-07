"""Tests para normalización de teléfonos E.164."""
import pytest

from patients.services import normalize_phone, normalize_phone_safe


# ---------------------------------------------------------------------------
# normalize_phone — casos unitarios (sin DB)
# ---------------------------------------------------------------------------


class TestNormalizePhone:
    def test_ya_correcto(self):
        assert normalize_phone("+34612345678") == "+34612345678"

    def test_sin_plus(self):
        assert normalize_phone("34612345678") == "+34612345678"

    def test_espanol_sin_prefijo(self):
        assert normalize_phone("612345678") == "+34612345678"

    def test_con_espacios(self):
        assert normalize_phone("6 12 34 56 78") == "+34612345678"

    def test_numero_usa(self):
        assert normalize_phone("+1 212 555 1234") == "+12125551234"

    def test_invalido_letras(self):
        with pytest.raises(ValueError):
            normalize_phone("abc")

    def test_invalido_vacio(self):
        with pytest.raises(ValueError):
            normalize_phone("")

    def test_invalido_muy_corto(self):
        with pytest.raises(ValueError):
            normalize_phone("123")


class TestNormalizePhoneSafe:
    def test_valido_devuelve_normalizado(self):
        assert normalize_phone_safe("612345678") == "+34612345678"

    def test_invalido_devuelve_none(self):
        assert normalize_phone_safe("abc") is None

    def test_vacio_devuelve_none(self):
        assert normalize_phone_safe("") is None


# ---------------------------------------------------------------------------
# PatientSerializer.validate_phone
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPatientSerializerPhone:
    def test_acepta_y_normaliza_numero_espanol(self, clinic_a):
        from patients.serializers import PatientSerializer

        data = {
            "clinic": clinic_a.pk,
            "first_name": "Ana",
            "last_name": "García",
            "phone": "612345678",
            "email": "ana@example.com",
        }
        serializer = PatientSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["phone"] == "+34612345678"

    def test_rechaza_telefono_invalido(self, clinic_a):
        from patients.serializers import PatientSerializer

        data = {
            "clinic": clinic_a.pk,
            "first_name": "Ana",
            "last_name": "García",
            "phone": "abc",
            "email": "ana2@example.com",
        }
        serializer = PatientSerializer(data=data)
        assert not serializer.is_valid()
        assert "phone" in serializer.errors


# ---------------------------------------------------------------------------
# API: GET /api/patients/?phone=
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPatientFilterPhone:
    def _create_patient(self, clinic_a):
        from patients.models import Patient

        return Patient.objects.create(
            clinic=clinic_a,
            first_name="Carlos",
            last_name="López",
            email="carlos@example.com",
            phone="+34612345678",
        )

    def test_busqueda_normaliza_parametro(self, admin_client, clinic_a):
        self._create_patient(clinic_a)
        response = admin_client.get("/api/patients/?phone=612345678")
        assert response.status_code == 200
        data = response.json()
        results = data.get("results", data)
        assert len(results) == 1
        assert results[0]["phone"] == "+34612345678"

    def test_busqueda_con_plus_funciona(self, admin_client, clinic_a):
        self._create_patient(clinic_a)
        response = admin_client.get("/api/patients/?phone=%2B34612345678")
        assert response.status_code == 200
        data = response.json()
        results = data.get("results", data)
        assert len(results) == 1

    def test_busqueda_invalida_devuelve_lista_vacia(self, admin_client, clinic_a):
        self._create_patient(clinic_a)
        response = admin_client.get("/api/patients/?phone=invalido")
        assert response.status_code == 200
        data = response.json()
        results = data.get("results", data)
        assert results == []


# ---------------------------------------------------------------------------
# API: POST /api/patients/ — almacena normalizado
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPatientCreateNormalizesPhone:
    def test_post_normaliza_y_almacena(self, admin_client, clinic_a):
        from patients.models import Patient

        payload = {
            "clinic": clinic_a.pk,
            "first_name": "Luis",
            "last_name": "Martínez",
            "phone": "612345678",
            "email": "luis@example.com",
        }
        response = admin_client.post("/api/patients/", payload, format="json")
        assert response.status_code == 201
        patient = Patient.objects.get(email="luis@example.com")
        assert patient.phone == "+34612345678"
