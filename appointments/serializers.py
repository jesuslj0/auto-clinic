from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from appointments.models import Appointment, Professional, ProfessionalSchedule
from appointments.services import (
    AppointmentDomainError,
    create_appointment,
    validate_appointment_update,
)
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


class ProfessionalScheduleSerializer(serializers.ModelSerializer):
    day_of_week_display = serializers.CharField(source='get_day_of_week_display', read_only=True)

    class Meta:
        model = ProfessionalSchedule
        fields = ['id', 'professional', 'day_of_week', 'day_of_week_display', 'start_time', 'end_time', 'is_active']
        read_only_fields = ['id']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and start >= end:
            raise serializers.ValidationError({'end_time': 'La hora de fin debe ser posterior a la hora de inicio.'})
        return attrs


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
    schedules = ProfessionalScheduleSerializer(many=True, read_only=True)

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
            'schedules',
            'is_active',
            'accepts_online_booking',
            'buffer_minutes',
            'slot_granularity_minutes',
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
        clinic = attrs.get('clinic', getattr(self.instance, 'clinic', None))
        scheduled_at = attrs.get('scheduled_at', getattr(self.instance, 'scheduled_at', None))

        # Bug 4: La cita no puede ser en el pasado
        if scheduled_at and scheduled_at < timezone.now():
            raise serializers.ValidationError(
                {'scheduled_at': 'La cita no puede programarse en el pasado.'}
            )

        # Bug 2: El servicio debe estar activo
        if service and not service.is_active:
            raise serializers.ValidationError(
                {'service': 'El servicio seleccionado está desactivado.'}
            )

        # Bug 3: Multi-tenancy — el servicio debe pertenecer a la clínica de la cita
        if service and clinic and service.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                {'service': 'El servicio no pertenece a esta clínica.'}
            )

        # La elegibilidad del profesional (clínica, servicio que presta, horario,
        # ausencias, solapamiento) NO se decide aquí: es regla de negocio y vive
        # en services.py. El alta la aplica `create_appointment()`; la edición,
        # `validate_appointment_update()`. Este serializer solo orquesta.
        #
        # La API es vía online, así que `accepts_online_booking` sí se exige (con
        # o sin `professional` explícito en el payload).
        if self.instance:
            try:
                attrs.update(
                    validate_appointment_update(
                        self.instance, attrs, require_online_booking=True
                    )
                )
            except AppointmentDomainError as error:
                # Como ValidationError, para que `bulk-update` pueda recogerlo
                # por cita en vez de abortar el lote entero.
                raise serializers.ValidationError({'professional': error.detail['message']})

        return attrs

    def create(self, validated_data):
        # La creación real (cálculo de end_at, status inicial, auto-asignación de
        # profesional e historial) vive en el service compartido por API y panel.
        return create_appointment(require_online_booking=True, **validated_data)

    class Meta:
        model = Appointment
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'confirmation_token')
