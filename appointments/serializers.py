from rest_framework import serializers

from appointments.models import Appointment, Professional
from core.models import User
from services.models import Service


class UserMinimalSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.email

    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email', 'full_name']
        read_only_fields = ['id', 'first_name', 'last_name', 'email', 'full_name']


class ServiceMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'name', 'duration_minutes', 'price', 'is_active']
        read_only_fields = fields


class ProfessionalSerializer(serializers.ModelSerializer):
    user_info = UserMinimalSerializer(source='user', read_only=True)
    professional_type_display = serializers.CharField(source='get_professional_type_display', read_only=True)
    services_detail = ServiceMinimalSerializer(source='services', many=True, read_only=True)
    service_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Service.objects.all(),
        source='services',
        write_only=True,
        required=False,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        clinic = attrs.get('clinic', getattr(self.instance, 'clinic', None))
        services = attrs.get('services', None)
        if services and clinic:
            for service in services:
                if service.clinic_id != clinic.pk:
                    raise serializers.ValidationError(
                        {'service_ids': f'El servicio "{service.name}" no pertenece a esta clínica.'}
                    )
        return attrs

    class Meta:
        model = Professional
        fields = [
            'id',
            'user',
            'user_info',
            'clinic',
            'professional_type',
            'professional_type_display',
            'services_detail',
            'service_ids',
        ]
        read_only_fields = ['id']


class AppointmentSerializer(serializers.ModelSerializer):
    patient_phone = serializers.CharField(source='patient.phone', read_only=True, default='')
    patient_name = serializers.SerializerMethodField()
    service_name = serializers.CharField(source='service.name', read_only=True, default='')
    professional_name = serializers.SerializerMethodField()
    professional_type = serializers.CharField(
        source='professional.professional_type', read_only=True, default=''
    )
    professional_type_display = serializers.CharField(
        source='professional.get_professional_type_display', read_only=True, default=''
    )

    def get_patient_name(self, obj):
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}".strip()
        return ''

    def get_professional_name(self, obj):
        if obj.professional:
            return str(obj.professional)
        return ''

    def validate(self, attrs):
        attrs = super().validate(attrs)
        professional = attrs.get('professional', getattr(self.instance, 'professional', None))
        service = attrs.get('service', getattr(self.instance, 'service', None))

        if professional and service and not professional.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError(
                {'professional': 'El profesional seleccionado no ofrece el servicio indicado.'}
            )
        return attrs

    class Meta:
        model = Appointment
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'confirmation_token')
