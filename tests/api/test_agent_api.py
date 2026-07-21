"""
API tests for agent app:
  /api/agent/memory/    (AgentMemoryViewSet)
  /api/agent/errors/    (WorkflowErrorViewSet — READ ONLY)
  /api/agent/sessions/  (ConversationSessionViewSet)
"""
import pytest

from agent.models import AgentMemory, ChatMessage, ConversationSession, WorkflowError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_memory_a(db, clinic_a):
    return AgentMemory.objects.create(
        clinic=clinic_a,
        session_id="+34600000001",
        messages=[{"role": "user", "content": "Hello"}],
    )


@pytest.fixture
def agent_memory_b(db, clinic_b):
    return AgentMemory.objects.create(
        clinic=clinic_b,
        session_id="+34600000099",
        messages=[{"role": "user", "content": "Hola"}],
    )


@pytest.fixture
def workflow_error_a(db):
    return WorkflowError.objects.create(
        workflow="booking-flow",
        workflow_name="Booking Workflow",
        node_name="ParseNode",
        error_message="Unexpected payload format",
        phone="+34600000002",
    )


@pytest.fixture
def session_a(db, clinic_a):
    return ConversationSession.objects.create(
        phone="+34600000010",
        clinic=clinic_a,
        session_data={"step": "greeting"},
    )


@pytest.fixture
def session_b(db, clinic_b):
    return ConversationSession.objects.create(
        phone="+34600000020",
        clinic=clinic_b,
        session_data={"step": "booking"},
    )


# ---------------------------------------------------------------------------
# AgentMemory
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAgentMemoryViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/agent/memory/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, agent_memory_a):
        response = admin_client.get("/api/agent/memory/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, agent_memory_a):
        response = staff_client.get("/api/agent/memory/")
        assert response.status_code == 200

    def test_other_clinic_memory_is_hidden(self, admin_client, agent_memory_a, agent_memory_b):
        response = admin_client.get("/api/agent/memory/")
        assert response.status_code == 200
        returned = {str(item["id"]) for item in response.data["results"]}
        assert str(agent_memory_a.pk) in returned
        assert str(agent_memory_b.pk) not in returned


@pytest.mark.django_db
class TestAgentMemoryViewSetCreate:
    def test_admin_can_create(self, admin_client):
        data = {
            "session_id": "+34699000001",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        response = admin_client.post("/api/agent/memory/", data, format="json")
        assert response.status_code == 201
        assert response.data["session_id"] == "+34699000001"

    def test_staff_can_create(self, staff_client):
        data = {
            "session_id": "+34699000002",
            "messages": [],
        }
        response = staff_client.post("/api/agent/memory/", data, format="json")
        assert response.status_code == 201

    def test_create_requires_session_id(self, admin_client):
        response = admin_client.post("/api/agent/memory/", {}, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestAgentMemoryViewSetRetrieve:
    def test_retrieve(self, admin_client, agent_memory_a):
        response = admin_client.get(f"/api/agent/memory/{agent_memory_a.pk}/")
        assert response.status_code == 200
        assert str(response.data["id"]) == str(agent_memory_a.pk)

    def test_retrieve_other_clinic_memory_returns_404(self, admin_client, agent_memory_b):
        response = admin_client.get(f"/api/agent/memory/{agent_memory_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestAgentMemoryViewSetUpdate:
    def test_can_update_messages(self, admin_client, agent_memory_a):
        new_messages = [{"role": "assistant", "content": "Updated"}]
        response = admin_client.patch(
            f"/api/agent/memory/{agent_memory_a.pk}/",
            {"messages": new_messages},
            format="json",
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestAgentMemoryViewSetDelete:
    def test_can_delete(self, admin_client, agent_memory_a):
        pk = agent_memory_a.pk
        response = admin_client.delete(f"/api/agent/memory/{agent_memory_a.pk}/")
        assert response.status_code == 204
        assert not AgentMemory.objects.filter(pk=pk).exists()


@pytest.mark.django_db
class TestAgentMemoryExport:
    def test_export_returns_list(self, admin_client, agent_memory_a):
        response = admin_client.get("/api/agent/memory/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)


# ---------------------------------------------------------------------------
# WorkflowError (read-only)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWorkflowErrorViewSetAppendOnly:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/agent/errors/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, workflow_error_a):
        response = admin_client.get("/api/agent/errors/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_list(self, staff_client, workflow_error_a):
        response = staff_client.get("/api/agent/errors/")
        assert response.status_code == 200

    def test_retrieve(self, admin_client, workflow_error_a):
        response = admin_client.get(f"/api/agent/errors/{workflow_error_a.pk}/")
        assert response.status_code == 200
        assert str(response.data["id"]) == str(workflow_error_a.pk)

    def test_post_allowed(self, admin_client):
        """n8n registra errores con POST; debe estar permitido."""
        response = admin_client.post("/api/agent/errors/", {
            "workflow": "test",
            "error_message": "test error",
        })
        assert response.status_code == 201

    def test_patch_not_allowed(self, admin_client, workflow_error_a):
        response = admin_client.patch(
            f"/api/agent/errors/{workflow_error_a.pk}/",
            {"workflow": "changed"},
        )
        assert response.status_code == 405

    def test_delete_not_allowed(self, admin_client, workflow_error_a):
        response = admin_client.delete(f"/api/agent/errors/{workflow_error_a.pk}/")
        assert response.status_code == 405

    def test_export_returns_list(self, admin_client, workflow_error_a):
        response = admin_client.get("/api/agent/errors/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)


# ---------------------------------------------------------------------------
# ConversationSession
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConversationSessionViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/agent/sessions/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, session_a):
        response = admin_client.get("/api/agent/sessions/")
        assert response.status_code == 200

    def test_staff_can_list(self, staff_client, session_a):
        response = staff_client.get("/api/agent/sessions/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, session_a, session_b):
        response = admin_client.get("/api/agent/sessions/")
        ids = [str(s["id"]) for s in response.data["results"]]
        assert str(session_a.pk) in ids
        assert str(session_b.pk) not in ids

    def test_clinic_b_cannot_see_clinic_a_sessions(self, client_b, session_a):
        response = client_b.get("/api/agent/sessions/")
        ids = [str(s["id"]) for s in response.data["results"]]
        assert str(session_a.pk) not in ids

    def test_superuser_sees_all(self, superuser_client, session_a, session_b):
        response = superuser_client.get("/api/agent/sessions/")
        ids = [str(s["id"]) for s in response.data["results"]]
        assert str(session_a.pk) in ids
        assert str(session_b.pk) in ids


@pytest.mark.django_db
class TestConversationSessionViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "phone": "+34611000001",
            "clinic": clinic_a.pk,
            "session_data": {"step": "init"},
        }
        response = admin_client.post("/api/agent/sessions/", data, format="json")
        assert response.status_code == 201
        assert response.data["phone"] == "+34611000001"

    def test_create_requires_phone(self, admin_client):
        response = admin_client.post("/api/agent/sessions/", {}, format="json")
        assert response.status_code == 400

    def test_phone_unique_constraint_enforced(self, admin_client, session_a):
        data = {"phone": session_a.phone}
        response = admin_client.post("/api/agent/sessions/", data, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestConversationSessionViewSetRetrieve:
    def test_retrieve_own_clinic_session(self, admin_client, session_a):
        response = admin_client.get(f"/api/agent/sessions/{session_a.pk}/")
        assert response.status_code == 200
        assert str(response.data["id"]) == str(session_a.pk)

    def test_retrieve_other_clinic_session_returns_404(self, admin_client, session_b):
        response = admin_client.get(f"/api/agent/sessions/{session_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestConversationSessionViewSetUpdate:
    def test_can_update_session_data(self, admin_client, session_a):
        response = admin_client.patch(
            f"/api/agent/sessions/{session_a.pk}/",
            {"session_data": {"step": "confirmed"}},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["session_data"]["step"] == "confirmed"


@pytest.mark.django_db
class TestConversationSessionViewSetDelete:
    def test_can_delete(self, admin_client, session_a):
        pk = session_a.pk
        response = admin_client.delete(f"/api/agent/sessions/{session_a.pk}/")
        assert response.status_code == 204
        assert not ConversationSession.objects.filter(pk=pk).exists()

    def test_cannot_delete_other_clinic_session(self, admin_client, session_b):
        response = admin_client.delete(f"/api/agent/sessions/{session_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestConversationSessionBulkCreate:
    def test_bulk_create(self, admin_client, clinic_a):
        payload = [
            {"phone": f"+346{i:08d}", "clinic": clinic_a.pk}
            for i in range(3)
        ]
        response = admin_client.post("/api/agent/sessions/bulk-create/", payload, format="json")
        assert response.status_code == 201
        assert len(response.data) == 3


@pytest.mark.django_db
class TestConversationSessionBulkUpdate:
    def test_bulk_update(self, admin_client, session_a):
        payload = [{"id": str(session_a.pk), "session_data": {"step": "done"}}]
        response = admin_client.patch("/api/agent/sessions/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["updated"]) == 1
        assert response.data["updated"][0]["session_data"]["step"] == "done"


@pytest.mark.django_db
class TestConversationSessionStatusAction:
    def test_status_action_returns_info(self, admin_client, session_a):
        response = admin_client.get(f"/api/agent/sessions/{session_a.pk}/status/")
        assert response.status_code == 200
        assert response.data["id"] == str(session_a.pk)
        assert response.data["phone"] == session_a.phone
        assert "has_appointment_context" in response.data
        assert "updated_at" in response.data

    def test_status_action_other_clinic_session_returns_404(self, admin_client, session_b):
        response = admin_client.get(f"/api/agent/sessions/{session_b.pk}/status/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestConversationSessionExport:
    def test_export_returns_list(self, admin_client, session_a):
        response = admin_client.get("/api/agent/sessions/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, session_a, session_b):
        response = admin_client.get("/api/agent/sessions/export/")
        ids = [str(s["id"]) for s in response.data]
        assert str(session_a.pk) in ids
        assert str(session_b.pk) not in ids


# ---------------------------------------------------------------------------
# ChatMessage (historial del panel de chats)
# ---------------------------------------------------------------------------

def _message_payload(**overrides):
    data = {
        "phone": "+34600000010",
        "direction": "inbound",
        "sender": "patient",
        "body": "Hola, quiero pedir cita",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestChatMessageIngest:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.post("/api/agent/messages/", _message_payload(), format="json")
        assert response.status_code in (401, 403)

    def test_creates_session_on_first_message(self, admin_client, clinic_a):
        response = admin_client.post("/api/agent/messages/", _message_payload(), format="json")
        assert response.status_code == 201
        session = ConversationSession.objects.get(clinic=clinic_a, phone="+34600000010")
        assert session.messages.count() == 1

    def test_reuses_session_and_normalizes_phone(self, admin_client, clinic_a):
        admin_client.post("/api/agent/messages/", _message_payload(), format="json")
        # Mismo número escrito de otra forma: debe caer en el mismo hilo.
        admin_client.post(
            "/api/agent/messages/",
            _message_payload(phone="600 000 010", body="¿Hola?"),
            format="json",
        )
        sessions = ConversationSession.objects.filter(clinic=clinic_a, phone="+34600000010")
        assert sessions.count() == 1
        assert sessions.first().messages.count() == 2

    def test_duplicate_wa_message_id_is_ignored(self, admin_client, clinic_a):
        payload = _message_payload(wa_message_id="wamid.ABC123")
        first = admin_client.post("/api/agent/messages/", payload, format="json")
        second = admin_client.post("/api/agent/messages/", payload, format="json")
        assert first.status_code == 201
        assert second.status_code == 201
        assert ChatMessage.objects.filter(wa_message_id="wamid.ABC123").count() == 1
        session = ConversationSession.objects.get(clinic=clinic_a, phone="+34600000010")
        # El reintento tampoco debe inflar el contador de no leídos.
        assert session.unread_count == 1

    def test_requires_body_or_media(self, admin_client):
        response = admin_client.post(
            "/api/agent/messages/", _message_payload(body=""), format="json"
        )
        assert response.status_code == 400

    def test_requires_phone_or_session(self, admin_client):
        payload = _message_payload()
        del payload["phone"]
        response = admin_client.post("/api/agent/messages/", payload, format="json")
        assert response.status_code == 400

    def test_links_existing_patient_by_phone(self, admin_client, clinic_a, patient_a):
        admin_client.post(
            "/api/agent/messages/", _message_payload(phone=patient_a.phone), format="json"
        )
        session = ConversationSession.objects.get(clinic=clinic_a, phone=patient_a.phone)
        assert session.patient_id == patient_a.pk

    def test_cannot_write_into_another_clinic_session(self, admin_client, session_b):
        payload = _message_payload()
        del payload["phone"]
        payload["session"] = str(session_b.pk)
        response = admin_client.post("/api/agent/messages/", payload, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestChatMessageSessionCounters:
    def test_inbound_updates_preview_and_unread(self, admin_client, clinic_a):
        admin_client.post(
            "/api/agent/messages/", _message_payload(body="Buenos días"), format="json"
        )
        session = ConversationSession.objects.get(clinic=clinic_a, phone="+34600000010")
        assert session.last_message_preview == "Buenos días"
        assert session.unread_count == 1
        assert session.last_message_at is not None

    def test_outbound_does_not_increase_unread(self, admin_client, clinic_a):
        admin_client.post(
            "/api/agent/messages/",
            _message_payload(direction="outbound", sender="agent", body="Claro, dime"),
            format="json",
        )
        session = ConversationSession.objects.get(clinic=clinic_a, phone="+34600000010")
        assert session.unread_count == 0
        assert session.last_message_preview == "Claro, dime"

    def test_mark_session_read_resets_counter(self, admin_client, clinic_a):
        from agent.services import mark_session_read

        admin_client.post("/api/agent/messages/", _message_payload(), format="json")
        session = ConversationSession.objects.get(clinic=clinic_a, phone="+34600000010")
        mark_session_read(session)
        session.refresh_from_db()
        assert session.unread_count == 0
        assert session.messages.filter(read_at__isnull=True).count() == 0


@pytest.mark.django_db
class TestChatMessageIsolationAndMethods:
    def test_list_only_own_clinic(self, admin_client, clinic_b, session_b):
        from agent.services import record_message

        admin_client.post("/api/agent/messages/", _message_payload(), format="json")
        # El mensaje ajeno se crea por el ORM y no con `client_b`: ese fixture
        # comparte instancia de APIClient con `admin_client` y reautenticaría
        # la sesión del test.
        record_message(
            clinic=clinic_b,
            session=session_b,
            direction="inbound",
            sender="patient",
            body="Mensaje de otra clínica",
        )
        response = admin_client.get("/api/agent/messages/")
        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["body"] == "Hola, quiero pedir cita"

    def test_is_append_only(self, admin_client, clinic_a):
        admin_client.post("/api/agent/messages/", _message_payload(), format="json")
        message = ChatMessage.objects.get()
        assert admin_client.patch(
            f"/api/agent/messages/{message.pk}/", {"body": "editado"}, format="json"
        ).status_code == 405
        assert admin_client.delete(f"/api/agent/messages/{message.pk}/").status_code == 405
