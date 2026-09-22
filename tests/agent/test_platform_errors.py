"""Tests de POST /api/agent/platform-errors/ (manejador de errores global de n8n)."""
import pytest

from agent.models import WorkflowError

URL = "/api/agent/platform-errors/"
ERRORS_KEY = "test-errors-secret-key"
MASTER_KEY = "test-master-secret-key"

PAYLOAD = {
    "workflow": "zG25BeZcZydZwM9f",
    "workflow_name": "WA-Inbound-Orchestrator",
    "node_name": "Cargar Sesion",
    "error_message": "Bad gateway",
    "payload": {"execution_id": "2900"},
}


@pytest.fixture(autouse=True)
def agent_keys(settings):
    settings.AGENT_ERRORS_API_KEY = ERRORS_KEY
    settings.AGENT_MASTER_API_KEY = MASTER_KEY


def post(api_client, data, key=ERRORS_KEY):
    headers = {"HTTP_AUTHORIZATION": f"Api-Key {key}"} if key else {}
    return api_client.post(URL, data, format="json", **headers)


@pytest.mark.django_db
class TestPlatformWorkflowErrorAuth:
    def test_correct_key_creates_error_without_clinic(self, api_client):
        response = post(api_client, PAYLOAD)

        assert response.status_code == 201
        error = WorkflowError.objects.get(id=response.data["id"])
        assert error.clinic is None
        assert error.node_name == "Cargar Sesion"
        assert error.payload == {"execution_id": "2900"}

    def test_wrong_key_returns_403(self, api_client):
        response = post(api_client, PAYLOAD, key="wrong-key")

        assert response.status_code == 403
        assert not WorkflowError.objects.exists()

    def test_missing_header_returns_403(self, api_client):
        response = post(api_client, PAYLOAD, key=None)

        assert response.status_code == 403

    def test_master_key_is_not_accepted(self, api_client):
        response = post(api_client, PAYLOAD, key=MASTER_KEY)

        assert response.status_code == 403

    def test_clinic_key_is_not_accepted(self, api_client, clinic_a):
        response = post(api_client, PAYLOAD, key=str(clinic_a.agent_api_key))

        assert response.status_code == 403

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, api_client, method):
        response = getattr(api_client, method)(
            URL, HTTP_AUTHORIZATION=f"Api-Key {ERRORS_KEY}"
        )

        assert response.status_code in (403, 405)

    def test_endpoint_closed_when_key_not_configured(self, api_client, settings):
        settings.AGENT_ERRORS_API_KEY = ""
        response = post(api_client, PAYLOAD, key="")

        assert response.status_code == 403
        assert not WorkflowError.objects.exists()


@pytest.mark.django_db
class TestPlatformWorkflowErrorClinicResolution:
    def test_resolves_clinic_by_clinic_id(self, api_client, clinic_a):
        response = post(api_client, {**PAYLOAD, "clinic_id": clinic_a.clinic_id})

        assert response.status_code == 201
        assert WorkflowError.objects.get().clinic == clinic_a

    def test_resolves_clinic_by_phone_number_id(self, api_client, clinic_a, clinic_b):
        clinic_b.whatsapp_phone_number_id = "12345678901"
        clinic_b.save(update_fields=["whatsapp_phone_number_id"])

        response = post(api_client, {**PAYLOAD, "phone_number_id": "12345678901"})

        assert response.status_code == 201
        assert WorkflowError.objects.get().clinic == clinic_b

    def test_unknown_clinic_is_stored_without_clinic(self, api_client, clinic_a):
        response = post(api_client, {**PAYLOAD, "clinic_id": "no-existe"})

        assert response.status_code == 201
        assert WorkflowError.objects.get().clinic is None

    def test_blank_phone_number_id_does_not_match_clinics_without_one(self, api_client, clinic_a):
        # clinic_a no tiene whatsapp_phone_number_id: un '' no debe atribuírselo.
        response = post(api_client, {**PAYLOAD, "phone_number_id": ""})

        assert response.status_code == 201
        assert WorkflowError.objects.get().clinic is None


@pytest.mark.django_db
class TestPlatformWorkflowErrorValidation:
    def test_long_fields_are_truncated_not_rejected(self, api_client):
        response = post(api_client, {
            **PAYLOAD,
            "node_name": "n" * 300,
            "phone": "+34" + "6" * 40,
        })

        assert response.status_code == 201
        error = WorkflowError.objects.get()
        assert len(error.node_name) == 100
        assert len(error.phone) == 20

    def test_error_message_is_required(self, api_client):
        data = {k: v for k, v in PAYLOAD.items() if k != "error_message"}

        response = post(api_client, data)

        assert response.status_code == 400
