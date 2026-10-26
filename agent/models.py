import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

from agent.files import chat_media_upload_to
from clinical.files import clinical_media_storage
from core.models import Clinic

# WhatsApp solo permite texto libre dentro de las 24 h siguientes al último
# mensaje del usuario. Fuera de esa ventana hay que usar plantillas aprobadas.
CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
# Cuánto se mantiene el aviso «el agente está escribiendo…» sin respuesta.
AGENT_TYPING_WINDOW = timedelta(seconds=60)


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
        verbose_name = 'memoria del agente'
        verbose_name_plural = 'memorias del agente'
        indexes = [
            models.Index(fields=["session_id"], name="idx_agent_memory_session"),
        ]

    def __str__(self):
        return f"Memoria sesión {self.session_id}"


class WorkflowError(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, null=True, blank=True, db_column="clinic_id"
    )
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
        verbose_name = 'error de workflow'
        verbose_name_plural = 'errores de workflow'
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
    guardian = models.ForeignKey(
        'patients.Guardian',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations',
        help_text="Contacto responsable, si el número es el de uno y no hay ficha con ese teléfono.",
    )
    session_data = models.JSONField(default=dict)
    last_interaction = models.DateTimeField(null=True, blank=True)
    appointment_context = models.JSONField(default=dict)

    # Cabecera del hilo en el panel (denormalizado desde ChatMessage).
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_preview = models.CharField(max_length=280, blank=True)
    unread_count = models.PositiveIntegerField(default=0)

    is_test = models.BooleanField(
        default=False,
        help_text=(
            "Conversación del banco de pruebas del panel, no de un paciente "
            "real. Se guarda igual, pero queda fuera de la bandeja de chats."
        ),
    )

    agent_paused = models.BooleanField(
        default=False,
        help_text=(
            "Cuando un humano toma el hilo, el agente deja de responder para "
            "que no contesten los dos a la vez."
        ),
    )
    last_staff_message_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Último mensaje escrito por una persona del staff. Abre una pausa "
            "temporal del agente que se cierra sola por inactividad."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation_sessions"
        verbose_name = 'sesión de conversación'
        verbose_name_plural = 'sesiones de conversación'
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

    # -- Control del agente ------------------------------------------------
    #
    # Hay dos pausas distintas y conviene no confundirlas:
    #
    #   * `agent_paused` es explícita e indefinida. La activa el staff con el
    #     interruptor y solo el staff la quita.
    #   * La que abre `last_staff_message_at` es automática y temporal: escribir
    #     en el hilo aparta al agente un rato para que no conteste encima, y se
    #     cierra sola por inactividad.

    @property
    def handoff_expires_at(self):
        """Cuándo retoma el agente tras el último mensaje del staff."""
        if self.last_staff_message_at is None or self.clinic is None:
            return None
        timeout = self.clinic.agent_handoff_timeout_seconds
        if not timeout:
            return None
        return self.last_staff_message_at + timedelta(seconds=timeout)

    @property
    def is_handoff_active(self):
        """Si la pausa automática por intervención humana sigue vigente."""
        expires_at = self.handoff_expires_at
        return expires_at is not None and timezone.now() < expires_at

    @property
    def agent_should_reply(self):
        """Respuesta única a «¿contesta el agente en este hilo ahora mismo?».

        Es la que consulta n8n antes de generar nada, y la que decide qué
        estado pinta el panel.
        """
        if self.clinic is None or not self.clinic.agent_enabled:
            return False
        return not self.agent_paused and not self.is_handoff_active

    # -- «El agente está escribiendo…» -------------------------------------

    @property
    def agent_typing_seconds(self):
        """Segundos que le quedan al aviso «el agente está escribiendo», o 0.

        No lo notifica n8n: se deduce. Si el último mensaje del hilo es del
        paciente, el agente va a contestar (`agent_should_reply`) y ha pasado
        poco, el agente está en ello. Pasado `AGENT_TYPING_WINDOW` se da por
        perdido (n8n caído, error del modelo): mejor dejar de decirlo que
        mostrar un «escribiendo…» eterno.
        """
        if self.last_interaction is None or self.last_message_at is None:
            return 0
        if self.last_message_at > self.last_interaction or not self.agent_should_reply:
            return 0
        remaining = (self.last_interaction + AGENT_TYPING_WINDOW - timezone.now()).total_seconds()
        return max(0, int(remaining))

    # -- Ventana de servicio de WhatsApp ------------------------------------

    @property
    def customer_window_expires_at(self):
        """Fin del plazo de 24 h para responder con texto libre."""
        if self.last_interaction is None:
            return None
        return self.last_interaction + CUSTOMER_SERVICE_WINDOW

    @property
    def can_send_free_text(self):
        """Si Meta aceptaría ahora un mensaje de texto normal.

        Fuera de la ventana solo pasan plantillas aprobadas, así que el panel
        bloquea el cuadro de texto en vez de dejar escribir para nada.
        """
        expires_at = self.customer_window_expires_at
        return expires_at is not None and timezone.now() < expires_at


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
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="Cuándo lo leyó la clínica en el panel (entrantes)."
    )
    # Acuses de WhatsApp de los salientes (webhook `statuses` de Meta). Van aparte
    # de `read_at`, que es la lectura del staff, no la del paciente.
    delivered_at = models.DateTimeField(
        null=True, blank=True, help_text="Cuándo llegó al móvil del paciente (✓✓)."
    )
    seen_at = models.DateTimeField(
        null=True, blank=True, help_text="Cuándo lo leyó el paciente (✓✓ azul)."
    )

    class Meta:
        db_table = "chat_messages"
        verbose_name = 'mensaje de chat'
        verbose_name_plural = 'mensajes de chat'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=["session", "created_at"], name="idx_chat_msg_session"),
            models.Index(fields=["clinic", "-created_at"], name="idx_chat_msg_clinic"),
        ]

    def __str__(self):
        return f"{self.get_sender_display()} → {self.body[:40]}"

    @property
    def media(self):
        """El adjunto del mensaje, o `None` si no tiene (o aún no ha llegado)."""
        from django.core.exceptions import ObjectDoesNotExist

        try:
            return self.attachment
        except ObjectDoesNotExist:
            return None

    @property
    def display_body(self) -> str:
        """El texto que escribió alguien, sin el marcador que pone n8n.

        Un adjunto sin pie de foto llega con `body` = «[image]», «[audio]»… para
        que la API no lo rechace por vacío. Eso no es texto del paciente y no se
        enseña: la burbuja ya muestra el adjunto.
        """
        text = self.body.strip()
        if text == f'[{self.message_type}]':
            return ''
        return text


class ChatAttachmentImmutable(Exception):
    """Se ha intentado modificar un adjunto de chat ya guardado."""


class ChatAttachment(models.Model):
    """Foto o nota de voz que mandó un paciente por WhatsApp.

    Es dato de salud y se guarda como tal (ver `agent/files.py`): bucket privado,
    clave UUID, contenido validado y, si es imagen, reescrita sin metadatos. Se
    sirve solo con sesión del staff de la clínica, con URL firmada de vida corta
    y dejando `AccessLog` (`agent.media`).

    **Irremplazable.** Un mensaje tiene como mucho un adjunto (`OneToOne`: la
    base de datos rechaza el segundo) y el adjunto no se modifica nunca: ni el
    ORM (`save()` de una fila existente) ni SQL crudo (trigger de la migración
    0009). Lo que el paciente mandó es lo que queda.

    Vive aparte de `ChatMessage` para que el mensaje siga siendo de solo
    inserción: el binario llega después que el texto (n8n lo descarga de Meta y
    lo sube), y añadirlo no tiene que tocar la fila del mensaje.
    """

    class Kind(models.TextChoices):
        IMAGE = 'image', 'Imagen'
        AUDIO = 'audio', 'Audio'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.OneToOneField(
        ChatMessage, on_delete=models.CASCADE, related_name='attachment'
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.FileField(
        upload_to=chat_media_upload_to,
        storage=clinical_media_storage,
        max_length=255,
        help_text='Clave del objeto en el bucket privado. Nunca una URL.',
    )
    mime_type = models.CharField(
        max_length=100, help_text='Tipo real del contenido, deducido al validar.'
    )
    size_bytes = models.PositiveBigIntegerField()
    checksum = models.CharField(
        max_length=71, help_text='sha256:<hexdigest> del fichero guardado.'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_attachments'
        verbose_name = 'adjunto de chat'
        verbose_name_plural = 'adjuntos de chat'

    def __str__(self):
        return f'{self.get_kind_display()} del mensaje {self.message_id}'

    @property
    def patient(self):
        """Paciente del hilo, si ya tiene ficha. Puede ser `None`."""
        return self.message.session.patient

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ChatAttachmentImmutable(
                f'El adjunto {self.pk} no se puede modificar: lo que mandó el paciente es lo que queda.'
            )
        if not self.file:
            raise ValidationError({'file': 'El adjunto necesita un fichero.'})

        # La validación vive aquí, no en la vista: cualquier camino de entrada
        # (API, shell, admin) pasa por el mismo filtro.
        from agent.files import prepare_chat_audio, prepare_chat_image

        prepare = prepare_chat_image if self.kind == self.Kind.IMAGE else prepare_chat_audio
        prepared = prepare(self.file)
        self.mime_type = prepared.mime_type
        self.size_bytes = prepared.size_bytes
        self.checksum = prepared.checksum
        # Se guarda el contenido PREPARADO (imagen sin metadatos), no el subido.
        # El nombre es irrelevante: `chat_media_upload_to` pone la clave.
        self.file = ContentFile(prepared.content, name=f'upload{prepared.extension}')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ChatAttachmentImmutable('Los adjuntos de chat no se borran uno a uno.')


class AgentProfile(models.Model):
    """Cómo se presenta y cómo habla el agente de WhatsApp de una clínica.

    Solo cambia el estilo: las reglas del agente (herramientas, no inventar
    datos, confirmar antes de citar) viven fijas en el prompt de n8n y este
    perfil nunca las sustituye. Por eso casi todo son opciones cerradas; el
    único texto libre de estilo es `style_notes`, y va con tope de longitud.

    n8n lo lee en cada ejecución (`GET /api/agent/profile/`), así que un
    cambio guardado se nota en el siguiente mensaje. Una clínica sin perfil
    recibe los valores por defecto, que reproducen el tono de siempre.
    """

    class Tone(models.TextChoices):
        CLOSE = 'cercano', 'Cercano'
        PROFESSIONAL = 'profesional', 'Profesional'
        FORMAL = 'formal', 'Formal'

    class AddressForm(models.TextChoices):
        TU = 'tu', 'De tú'
        USTED = 'usted', 'De usted'

    class EmojiUsage(models.TextChoices):
        NONE = 'ninguno', 'Sin emojis'
        MODERATE = 'moderado', 'Con moderación'

    WELCOME_MAX_LENGTH = 500
    STYLE_NOTES_MAX_LENGTH = 800

    clinic = models.OneToOneField(
        Clinic,
        on_delete=models.CASCADE,
        related_name='agent_profile',
        db_column='clinic_id',
    )
    agent_name = models.CharField(
        'nombre del agente',
        max_length=60,
        blank=True,
        help_text='Cómo se presenta ante los pacientes. Vacío: sin nombre propio.',
    )
    tone = models.CharField(
        'tono', max_length=20, choices=Tone.choices, default=Tone.PROFESSIONAL
    )
    address_form = models.CharField(
        'trato', max_length=10, choices=AddressForm.choices, default=AddressForm.TU
    )
    emoji_usage = models.CharField(
        'emojis', max_length=10, choices=EmojiUsage.choices, default=EmojiUsage.NONE
    )
    welcome_message = models.TextField(
        'presentación para contactos nuevos',
        max_length=WELCOME_MAX_LENGTH,
        blank=True,
        help_text='Lo que el agente cuenta en su primer mensaje a alguien que no es '
                  'paciente todavía. Lo adapta a lo que le pregunten, no lo copia literal.',
    )
    style_notes = models.TextField(
        'indicaciones de estilo',
        max_length=STYLE_NOTES_MAX_LENGTH,
        blank=True,
        help_text='Expresiones que usar o evitar, cómo despedirse… Solo estilo: '
                  'no cambia lo que el agente puede hacer.',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'agent_profile'
        verbose_name = 'perfil del agente'
        verbose_name_plural = 'perfiles del agente'

    def __str__(self):
        return f'Perfil del agente de {self.clinic_id}'

    @classmethod
    def for_clinic(cls, clinic) -> 'AgentProfile':
        """Perfil de la clínica; sin guardar y con los valores por defecto si no tiene."""
        try:
            return clinic.agent_profile
        except cls.DoesNotExist:
            return cls(clinic=clinic)


class WhatsAppTemplate(models.Model):
    """Plantilla de mensaje de WhatsApp de una clínica y su estado ante Meta.

    Fuera de las 24 h desde el último mensaje del paciente, WhatsApp solo deja
    escribir con una plantilla aprobada. El texto se redacta aquí, se envía a
    revisión y Meta responde con un estado; el nuestro es un reflejo del suyo
    (`wa_templates.sync_templates()`), nunca se inventa.

    Solo se edita mientras sea borrador o haya sido rechazada. Si Meta ya la
    tiene (`meta_template_id`), el nombre y el idioma se quedan fijos: son su
    identidad allí. Una plantilla importada con elementos que este panel no sabe
    pintar (botones, cabecera con imagen…) se muestra pero no se edita, para no
    descartarlos sin querer al reenviarla.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PENDING = 'pending', 'En revisión'
        APPROVED = 'approved', 'Aprobada'
        REJECTED = 'rejected', 'Rechazada'
        PAUSED = 'paused', 'Pausada'
        DISABLED = 'disabled', 'Desactivada'

    class Category(models.TextChoices):
        UTILITY = 'UTILITY', 'Utilidad'
        MARKETING = 'MARKETING', 'Marketing'

    class Language(models.TextChoices):
        ES = 'es', 'Español'
        ES_ES = 'es_ES', 'Español (España)'
        ES_MX = 'es_MX', 'Español (México)'
        CA = 'ca', 'Català'
        EU = 'eu', 'Euskara'
        GL = 'gl', 'Galego'
        EN = 'en', 'English'

    class Purpose(models.TextChoices):
        REMINDER_24H = 'reminder_24h', 'Recordatorio de cita (24 h antes)'
        BOOKING_CONFIRMED = 'booking_confirmed', 'Aviso de cita confirmada'
        BOOKING_CHANGED = 'booking_changed', 'Aviso de cambio de cita'
        BOOKING_CANCELLED = 'booking_cancelled', 'Aviso de cita cancelada'

    HEADER_MAX_LENGTH = 60
    BODY_MAX_LENGTH = 1024
    FOOTER_MAX_LENGTH = 60

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='whatsapp_templates',
        db_column='clinic_id',
    )
    name = models.CharField(
        'nombre',
        max_length=100,
        help_text='Identificador interno: minúsculas, números y guiones bajos. No se puede cambiar una vez enviada a Meta.',
    )
    language = models.CharField('idioma', max_length=10, choices=Language.choices, default=Language.ES)
    category = models.CharField(
        'categoría', max_length=20, choices=Category.choices, default=Category.UTILITY,
    )
    header_text = models.CharField('encabezado', max_length=HEADER_MAX_LENGTH, blank=True)
    body = models.TextField('mensaje', max_length=BODY_MAX_LENGTH)
    footer = models.CharField('pie', max_length=FOOTER_MAX_LENGTH, blank=True)
    body_examples = models.JSONField(
        'ejemplos de las variables',
        default=list,
        blank=True,
        help_text='Un valor de muestra por cada {{n}} del mensaje; Meta los pide para revisar la plantilla.',
    )

    purpose = models.CharField(
        'uso',
        max_length=20,
        choices=Purpose.choices,
        blank=True,
        help_text='Para qué la usa el sistema por su cuenta. Vacío: solo el personal la envía a mano.',
    )
    variable_map = models.JSONField(
        'datos de cada variable',
        default=dict,
        blank=True,
        help_text='Para el envío automático (recordatorio o aviso de cita): qué dato de la cita va en cada {{n}} '
                  '(claves de appointments.reminder_fields).',
    )
    status = models.CharField('estado', max_length=10, choices=Status.choices, default=Status.DRAFT)
    rejection_reason = models.CharField('motivo del rechazo', max_length=255, blank=True)
    meta_template_id = models.CharField(max_length=64, blank=True)
    has_unsupported_components = models.BooleanField(
        default=False,
        help_text='Importada de Meta con botones, imágenes u otros elementos que este panel no edita.',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    status_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'agent_whatsapp_template'
        verbose_name = 'plantilla de WhatsApp'
        verbose_name_plural = 'plantillas de WhatsApp'
        ordering = ['name', 'language']
        constraints = [
            models.UniqueConstraint(
                fields=['clinic', 'name', 'language'], name='uniq_wa_template_per_clinic_lang'
            ),
            # Una por uso y clínica: el sistema no tiene que elegir entre dos.
            models.UniqueConstraint(
                fields=['clinic', 'purpose'],
                condition=~models.Q(purpose=''),
                name='uniq_wa_template_purpose_per_clinic',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.language})'

    _VARIABLE = re.compile(r'\{\{\s*(\d+)\s*\}\}')

    @classmethod
    def variables_in(cls, text):
        """Números de variable `{{n}}` de un texto, sin repetir y en orden."""
        return sorted({int(n) for n in cls._VARIABLE.findall(text or '')})

    @property
    def variables(self):
        return self.variables_in(self.body)

    @property
    def is_editable(self):
        return (
            self.status in (self.Status.DRAFT, self.Status.REJECTED)
            and not self.has_unsupported_components
        )

    @property
    def identity_locked(self):
        """Meta ya la conoce: nombre e idioma son su clave allí y no se tocan."""
        return bool(self.meta_template_id)

    @property
    def is_deletable(self):
        """Solo un borrador que Meta no conoce; el resto vive también en Meta."""
        return self.status == self.Status.DRAFT and not self.meta_template_id

    def fill(self, values):
        """El mensaje con `values` puestos, tal como lo vería el paciente.

        Una variable sin valor se queda como ``{{n}}``.
        """
        values = list(values or [])

        def replace(match):
            index = int(match.group(1)) - 1
            return str(values[index]) if 0 <= index < len(values) and values[index] else match.group(0)

        return self._VARIABLE.sub(replace, self.body)

    def preview_body(self):
        """El mensaje con los ejemplos puestos."""
        return self.fill(self.body_examples)

    def full_text(self, values):
        """Encabezado, mensaje y pie con `values` puestos: lo que lee el paciente de punta a punta."""
        parts = [self.header_text, self.fill(values), self.footer]
        return '\n\n'.join(part for part in parts if part)

    def why_not_automatic(self):
        """Por qué no puede usarse en un envío automático (recordatorio o aviso de cita), o '' si puede."""
        if not self.is_sendable:
            return 'Tiene que estar aprobada por Meta.'
        if self.variables != list(range(1, len(self.variables) + 1)):
            return 'Sus variables deben numerarse seguidas desde {{1}}.'
        return ''

    def reminder_map_problem(self):
        """Por qué el mapeo de variables no sirve para enviar, o '' si está completo."""
        from appointments.reminder_fields import BY_KEY

        for number in self.variables:
            if (self.variable_map or {}).get(str(number)) not in BY_KEY:
                return f'Falta elegir qué dato va en {{{{{number}}}}}.'
        return ''

    @property
    def is_sendable(self):
        """Si el staff puede mandarla a un paciente: aprobada y con solo lo que el panel sabe rellenar."""
        return self.status == self.Status.APPROVED and not self.has_unsupported_components
