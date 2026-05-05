"""
Tests para ClinicAgent: autenticación por Api-Key de clínica y scoping multitenant.

Organización:
  - TestAgentClinicKeyAuthUnit   → prueba directa del método authenticate() (Paso 1)
  - TestAgentAuthHTTP            → tests 1-4 a nivel HTTP (requiere Paso 2)
  - TestClinicAgentScoping       → tests 5-9 scoping por clínica (requiere Paso 2 y 3)
"""
import uuid

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from core.authentication import AgentClinicKeyAuthentication, ClinicAgent


# ---------------------------------------------------------------------------
# Fixtures locales
# ---------------------------------------------------------------------------

@pytest.fixture
def session_a(db, clinic_a):
    from agent.models import ConversationSession
    return ConversationSession.objects.create(
        phone="+34600000010",
        clinic=clinic_a,
        session_data={"step": "greeting"},
    )


@pytest.fixture
def session_b(db, clinic_b):
    from agent.models import ConversationSession
    return ConversationSession.objects.create(
        phone="+34600000020",
        clinic=clinic_b,
        session_data={"step": "booking"},
    )


@pytest.fixture
def agent_client(api_client, clinic_a):
    """APIClient preconfigurado con la Api-Key de clinic_a."""
    api_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {clinic_a.agent_api_key}")
    return api_client


# ---------------------------------------------------------------------------
# Paso 1 — Pruebas unitarias del autenticador
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAgentClinicKeyAuthUnit:
    def test_valid_key_returns_clinic_agent(self, clinic_a):
        request = APIRequestFactory().get(
            '/', HTTP_AUTHORIZATION=f"Api-Key {clinic_a.agent_api_key}"
        )
        user, token = AgentClinicKeyAuthentication().authenticate(request)
        assert isinstance(user, ClinicAgent)
        assert user.clinic == clinic_a
        assert user.is_authenticated is True
        assert user.is_superuser is False
        assert user.role == 'agent'
        assert token == clinic_a

    def test_nonexistent_uuid_raises_authentication_failed(self, db):
        request = APIRequestFactory().get(
            '/', HTTP_AUTHORIZATION=f"Api-Key {uuid.uuid4()}"
        )
        with pytest.raises(AuthenticationFailed):
            AgentClinicKeyAuthentication().authenticate(request)

    def test_non_api_key_prefix_returns_none(self, db):
        request = APIRequestFactory().get(
            '/', HTTP_AUTHORIZATION="Token sometoken"
        )
        assert AgentClinicKeyAuthentication().authenticate(request) is None

    def test_no_header_returns_none(self, db):
        request = APIRequestFactory().get('/')
        assert AgentClinicKeyAuthentication().authenticate(request) is None

    def test_clinic_agent_str(self, clinic_a):
        assert str(ClinicAgent(clinic_a)) == f"ClinicAgent({clinic_a.clinic_id})"

    def test_clinic_agent_clinic_id_property(self, clinic_a):
        assert ClinicAgent(clinic_a).clinic_id == clinic_a.clinic_id


# ---------------------------------------------------------------------------
# Test 1 — Header válido autentica como ClinicAgent (HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_valid_api_key_returns_200(agent_client, patient_a):
    response = agent_client.get("/api/patients/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 2 — UUID inexistente → 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_invalid_api_key_returns_403(api_client):
    api_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {uuid.uuid4()}")
    response = api_client.get("/api/patients/")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test 3 — Token DRF sigue funcionando para usuarios normales
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_token_auth_still_works(api_client, admin_user):
    from rest_framework.authtoken.models import Token
    token = Token.objects.create(user=admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    response = api_client.get("/api/patients/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 4 — Sin header → requiere autenticación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_header_requires_auth(api_client):
    response = api_client.get("/api/patients/")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Tests 5-9 — Scoping por clínica
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestClinicAgentScoping:

    def test_appointments_own_clinic_only(self, agent_client, appointment_a, appointment_b):
        """Test 5: ClinicAgent ve citas propias y no las de la otra clínica."""
        response = agent_client.get("/api/appointments/")
        assert response.status_code == 200
        ids = [str(a["id"]) for a in response.data["results"]]
        assert str(appointment_a.id) in ids
        assert str(appointment_b.id) not in ids

    def test_patients_own_clinic_only(self, agent_client, patient_a, patient_b):
        """Test 6: ClinicAgent ve pacientes propios y no los de la otra clínica."""
        response = agent_client.get("/api/patients/")
        assert response.status_code == 200
        ids = [str(p["id"]) for p in response.data["results"]]
        assert str(patient_a.id) in ids
        assert str(patient_b.id) not in ids

    def test_sessions_own_clinic_only(self, agent_client, session_a, session_b):
        """Test 7: ClinicAgent ve sesiones propias y no las de la otra clínica."""
        response = agent_client.get("/api/agent/sessions/")
        assert response.status_code == 200
        ids = [str(s["id"]) for s in response.data["results"]]
        assert str(session_a.id) in ids
        assert str(session_b.id) not in ids

    def test_agent_can_create_patient(self, agent_client, clinic_a):
        """Test 8: ClinicAgent puede crear un paciente via POST."""
        data = {
            "clinic": clinic_a.pk,
            "first_name": "Agent",
            "last_name": "Created",
            "email": "agent.created@test.com",
            "phone": "+34600000099",
        }
        response = agent_client.post("/api/patients/", data, format="json")
        assert response.status_code == 201

    def test_agent_cannot_delete_appointment(self, agent_client, appointment_a):
        """Test 9: ClinicAgent no puede hacer DELETE en appointments."""
        response = agent_client.delete(f"/api/appointments/{appointment_a.id}/")
        assert response.status_code == 403
