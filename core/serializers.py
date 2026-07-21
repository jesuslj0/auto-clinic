from rest_framework import serializers

from appointments.models import Professional
from core.authentication import ClinicAgent
from core.models import Clinic, User


class ClinicScopedSerializerMixin:
    """Aislamiento multitenant en ESCRITURA.

    Un usuario con clínica asignada (o un `ClinicAgent`) siempre opera sobre la
    suya, aunque el payload traiga otro `clinic`. Solo el superusuario (o un
    usuario sin clínica) puede fijar la clínica libremente desde el payload.

    Se aplica en el serializer —no en `perform_create` del viewset— porque los
    bulk endpoints (`bulk-create`/`bulk-update` de `core.mixins`) llaman a
    `serializer.save()` directamente y se saltarían cualquier guardia del
    viewset. El serializer es el único punto de paso común a create, update,
    bulk-create y bulk-update.
    """

    def _enforce_clinic(self, validated_data):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None:
            return
        if isinstance(user, ClinicAgent):
            validated_data['clinic'] = user.clinic
        elif not user.is_superuser and getattr(user, 'clinic_id', None):
            validated_data['clinic'] = user.clinic

    def create(self, validated_data):
        self._enforce_clinic(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._enforce_clinic(validated_data)
        return super().update(instance, validated_data)


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = (
            'clinic_id', 'name', 'nif', 'is_active',
            'phone', 'email', 'website',
            'address', 'city', 'province', 'postal_code', 'country',
            'timezone', 'description', 'logo_url', 'logo',
            'whatsapp_phone_number_id',
            'api_type', 'api_url', 'api_key',
            'calendly_link', 'calendly_token', 'calendly_event_type_uuid',
            'google_calendar_id',
            'test_patient',
        )


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    professional_id = serializers.IntegerField(source='professional_profile.id', read_only=True)

    class Meta:
        model = User
        fields = (
            'id', 'email', 'password', 'first_name', 'last_name', 'clinic',
            'role', 'professional_id',
            'is_active', 'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        user.save()
        self._sync_professional(user)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        self._sync_professional(instance)
        return instance

    def _sync_professional(self, user):
        if not user.clinic_id or not user.role:
            return

        Professional.objects.update_or_create(
            user=user,
            defaults={'clinic': user.clinic},
        )


class AgentConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = (
            'clinic_id',
            'name',
            'timezone',
            'whatsapp_phone_number_id',
            'whatsapp_token',
            'whatsapp_verify_token',
            'agent_api_key',
        )
        read_only_fields = fields
