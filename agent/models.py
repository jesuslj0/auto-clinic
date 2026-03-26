import uuid

from django.db import models

from core.models import Clinic


class AgentMemory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_id = models.CharField(max_length=100)  # phone del paciente
    messages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_memory"
        indexes = [
            models.Index(fields=["session_id"], name="idx_agent_memory_session"),
        ]

    def __str__(self):
        return f"Memoria sesión {self.session_id}"


class WorkflowError(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.CharField(max_length=100, blank=True)
    workflow_name = models.CharField(max_length=100, blank=True)
    node_name = models.CharField(max_length=100, blank=True)
    error_message = models.TextField()
    phone = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(null=True, blank=True)
    input_data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_errors"
        indexes = [
            models.Index(fields=["workflow", "created_at"], name="idx_workflow_errors"),
        ]

    def __str__(self):
        return f"{self.workflow or self.workflow_name} – {self.created_at:%Y-%m-%d %H:%M}"


class ConversationSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, unique=True)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.SET_NULL, null=True, blank=True, db_column="clinic_id"
    )
    session_data = models.JSONField(default=dict)
    last_interaction = models.DateTimeField(null=True, blank=True)
    appointment_context = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation_sessions"

    def __str__(self):
        return f"Session {self.phone}"
