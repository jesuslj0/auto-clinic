from rest_framework import serializers

from agent.models import AgentMemory, ChatMessage, ConversationSession, WorkflowError
from agent.services import record_message
from core.models import Clinic
from core.serializers import ClinicScopedSerializerMixin


class AgentMemorySerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = AgentMemory
        fields = '__all__'
        read_only_fields = ('id', 'created_at')


class WorkflowErrorSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = WorkflowError
        fields = '__all__'
        read_only_fields = ('id', 'created_at')
        # La clínica sale de la Api-Key del agente; n8n no la manda.
        extra_kwargs = {'clinic': {'required': False}}


class PlatformWorkflowErrorSerializer(serializers.ModelSerializer):
    """Error enviado por el manejador global de n8n (sin Api-Key de clínica).

    La clínica es opcional y se resuelve aquí, en el servidor, por `clinic_id` o
    por `phone_number_id` de WhatsApp. Si no llega o no existe, el error se
    guarda sin clínica: perder el registro sería peor que no poder atribuirlo.
    Por la misma razón, los textos que excedan su columna se recortan en vez de
    rechazarse.
    """

    clinic_id = serializers.CharField(write_only=True, required=False, allow_blank=True)
    phone_number_id = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = WorkflowError
        fields = (
            'id', 'clinic', 'clinic_id', 'phone_number_id', 'workflow', 'workflow_name',
            'node_name', 'error_message', 'phone', 'payload', 'input_data', 'created_at',
        )
        read_only_fields = ('id', 'clinic', 'created_at')

    TRUNCATED_FIELDS = ('workflow', 'workflow_name', 'node_name', 'phone')

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        for name in self.TRUNCATED_FIELDS:
            value = data.get(name)
            if isinstance(value, str):
                data[name] = value[:WorkflowError._meta.get_field(name).max_length]
        return super().to_internal_value(data)

    def create(self, validated_data):
        clinic_id = validated_data.pop('clinic_id', '').strip()
        phone_number_id = validated_data.pop('phone_number_id', '').strip()
        clinic = None
        if clinic_id:
            clinic = Clinic.objects.filter(clinic_id=clinic_id).first()
        if clinic is None and phone_number_id:
            clinic = Clinic.objects.filter(whatsapp_phone_number_id=phone_number_id).first()
        return WorkflowError.objects.create(clinic=clinic, **validated_data)


class ConversationSessionSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    patient_name = serializers.CharField(source='patient.__str__', read_only=True)

    class Meta:
        model = ConversationSession
        fields = '__all__'
        read_only_fields = (
            'id',
            'updated_at',
            'last_message_at',
            'last_message_preview',
            'unread_count',
            # Lo fija el panel al abrir el chat de pruebas. Si n8n pudiera
            # escribirlo, un fallo suyo escondería hilos reales de la bandeja.
            'is_test',
        )

    def validate(self, attrs):
        # La unicidad es (clinic, phone), pero `clinic` no viaja en el payload
        # de un agente —lo impone _enforce_clinic al guardar—, así que el
        # UniqueTogetherValidator de DRF no puede verlo y el duplicado
        # explotaría como IntegrityError 500 en vez de un 400.
        attrs = super().validate(attrs)
        clinic = attrs.get('clinic') or self._forced_clinic()
        phone = attrs.get('phone')
        if clinic is None or phone is None:
            return attrs

        duplicates = ConversationSession.objects.filter(clinic=clinic, phone=phone)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                {'phone': 'Ya existe una conversación con este número en la clínica.'}
            )
        return attrs


class ChatMessageSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    """Ingesta de mensajes desde n8n.

    n8n manda el teléfono, no el id de sesión: `phone` es de escritura y el
    service resuelve (o crea) la conversación de esa clínica.
    """

    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    session = serializers.PrimaryKeyRelatedField(
        queryset=ConversationSession.objects.all(), required=False
    )
    # Sin `validators=[]`, el UniqueValidator de DRF devolvería 400 ante un
    # reenvío de Meta. Queremos lo contrario: que `record_message` reconozca el
    # duplicado y responda con el mensaje ya registrado.
    wa_message_id = serializers.CharField(
        max_length=128, required=False, allow_blank=True, allow_null=True, validators=[]
    )

    class Meta:
        model = ChatMessage
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'read_at')
        # La clínica se deduce del solicitante o de la sesión; n8n no la manda.
        extra_kwargs = {'clinic': {'required': False}}

    def validate(self, attrs):
        if not attrs.get('phone') and not attrs.get('session'):
            raise serializers.ValidationError(
                {'phone': 'Indica `phone` o `session` para saber a qué hilo pertenece.'}
            )
        if not attrs.get('body') and not attrs.get('media_url'):
            raise serializers.ValidationError(
                {'body': 'El mensaje necesita texto o un adjunto.'}
            )
        return attrs

    def create(self, validated_data):
        # Fija la clínica del ClinicAgent aunque el payload traiga otra; sin
        # esto n8n podría escribir en el hilo de una clínica ajena.
        self._enforce_clinic(validated_data)
        clinic = validated_data.pop('clinic', None)
        phone = validated_data.pop('phone', '')
        session = validated_data.pop('session', None)

        if session is not None:
            if clinic is not None and session.clinic_id != clinic.clinic_id:
                raise serializers.ValidationError(
                    {'session': 'La conversación pertenece a otra clínica.'}
                )
            clinic = session.clinic

        if clinic is None:
            raise serializers.ValidationError(
                {'clinic': 'No se puede determinar la clínica del mensaje.'}
            )

        return record_message(clinic=clinic, phone=phone, session=session, **validated_data)
