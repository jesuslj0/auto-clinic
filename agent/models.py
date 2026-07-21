import uuid

from django.db import models

from core.models import Clinic


class AgentMemory(models.Model):
    """Memoria de contexto del LLM, propiedad de n8n.

    Es efímera por naturaleza: n8n la trunca o resume para no desbordar la
    ventana de contexto. El historial que ve el staff en el panel vive en
    `ChatMessage`, que sí es un registro permanente.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, null=True, blank=True, db_column="clinic_id"
    )
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
    """Una conversación de WhatsApp entre un número y una clínica.

    Hace doble papel: estado del bot (`session_data`, `appointment_context`) y
    cabecera del hilo en el panel de chats (los denormalizados `last_message_*`
    y `unread_count` evitan tocar `ChatMessage` para pintar la lista).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, db_index=True)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.SET_NULL, null=True, blank=True, db_column="clinic_id"
    )
    patient = models.ForeignKey(
        'patients.Patient',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations',
        help_text="Paciente vinculado, si el número coincide con una ficha.",
    )
    session_data = models.JSONField(default=dict)
    last_interaction = models.DateTimeField(null=True, blank=True)
    appointment_context = models.JSONField(default=dict)

    # Cabecera del hilo en el panel (denormalizado desde ChatMessage).
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_preview = models.CharField(max_length=280, blank=True)
    unread_count = models.PositiveIntegerField(default=0)

    agent_paused = models.BooleanField(
        default=False,
        help_text=(
            "Cuando un humano toma el hilo, el agente deja de responder para "
            "que no contesten los dos a la vez."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation_sessions"
        # Antes `phone` era unique global, lo que impedía que dos clínicas
        # hablasen con el mismo número.
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "phone"], name="uniq_conversation_clinic_phone"
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "-last_message_at"], name="idx_conv_clinic_last_msg"),
        ]

    def __str__(self):
        return f"Session {self.phone}"


class ChatMessage(models.Model):
    """Un mensaje del hilo de WhatsApp. Append-only: es el registro que ve el staff.

    Lo escribe n8n vía `POST /api/agent/messages/`, tanto los entrantes del
    paciente como los que responde el agente.
    """

    class Direction(models.TextChoices):
        INBOUND = 'inbound', 'Entrante'
        OUTBOUND = 'outbound', 'Saliente'

    class Sender(models.TextChoices):
        PATIENT = 'patient', 'Paciente'
        AGENT = 'agent', 'Agente'
        STAFF = 'staff', 'Personal'

    class MessageType(models.TextChoices):
        TEXT = 'text', 'Texto'
        IMAGE = 'image', 'Imagen'
        AUDIO = 'audio', 'Audio'
        VIDEO = 'video', 'Vídeo'
        DOCUMENT = 'document', 'Documento'
        LOCATION = 'location', 'Ubicación'
        TEMPLATE = 'template', 'Plantilla'
        OTHER = 'other', 'Otro'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'En cola'
        SENT = 'sent', 'Enviado'
        DELIVERED = 'delivered', 'Entregado'
        READ = 'read', 'Leído'
        FAILED = 'failed', 'Fallido'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name='chat_messages', db_column="clinic_id"
    )
    session = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='messages'
    )

    direction = models.CharField(max_length=10, choices=Direction.choices)
    sender = models.CharField(max_length=10, choices=Sender.choices)
    body = models.TextField(blank=True)
    message_type = models.CharField(
        max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )

    media_url = models.URLField(blank=True)
    media_mime = models.CharField(max_length=100, blank=True)

    # ID del mensaje en la WhatsApp Cloud API. Único para que los reintentos
    # de Meta o de n8n no dupliquen el mensaje en el hilo.
    wa_message_id = models.CharField(max_length=128, blank=True, null=True, unique=True)

    status = models.CharField(max_length=12, choices=Status.choices, blank=True)
    error_message = models.TextField(blank=True)
    raw = models.JSONField(null=True, blank=True)

    sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Marca de tiempo original de WhatsApp."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_messages"
        ordering = ['created_at']
        indexes = [
            models.Index(fields=["session", "created_at"], name="idx_chat_msg_session"),
            models.Index(fields=["clinic", "-created_at"], name="idx_chat_msg_clinic"),
        ]

    def __str__(self):
        return f"{self.get_sender_display()} → {self.body[:40]}"
