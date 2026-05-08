from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from appointments.models import Appointment, Professional, ProfessionalSchedule
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
        status = attrs.get('status', getattr(self.instance, 'status', Appointment.Status.PENDING))

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

        # Bug 3: Multi-tenancy — profesional y servicio deben pertenecer a la clínica de la cita
        if professional and clinic and professional.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                {'professional': 'El profesional no pertenece a esta clínica.'}
            )
        if service and clinic and service.clinic_id != clinic.pk:
            raise serializers.ValidationError(
                {'service': 'El servicio no pertenece a esta clínica.'}
            )

        if professional and service and not professional.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError(
                {'professional': 'El profesional seleccionado no ofrece el servicio indicado.'}
            )

        # Bug 1: Validar día y rango horario laboral del profesional
        if professional and scheduled_at:
            day_names = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
            day_of_week = scheduled_at.weekday()
            try:
                schedule = ProfessionalSchedule.objects.get(
                    professional=professional,
                    day_of_week=day_of_week,
                    is_active=True,
                )
            except ProfessionalSchedule.DoesNotExist:
                raise serializers.ValidationError(
                    {'scheduled_at': f'El profesional no trabaja los {day_names[day_of_week]}.'}
                )
            appt_time = scheduled_at.astimezone(timezone.get_current_timezone()).time()
            if not (schedule.start_time <= appt_time < schedule.end_time):
                raise serializers.ValidationError({
                    'scheduled_at': (
                        f'La cita debe estar dentro del horario del profesional '
                        f'({schedule.start_time:%H:%M}–{schedule.end_time:%H:%M}).'
                    )
                })

        # Validación de solapamiento
        if (
            professional
            and scheduled_at
            and status in (Appointment.Status.PENDING, Appointment.Status.CONFIRMED)
        ):
            end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
            if not end_at:
                svc = service or getattr(self.instance, 'service', None)
                duration = svc.duration_minutes if svc else 30
                end_at = scheduled_at + timedelta(minutes=duration)

            exclude_pk = self.instance.pk if self.instance else None
            conflict = Appointment.find_overlap(professional, scheduled_at, end_at, exclude_pk=exclude_pk)
            if conflict:
                raise serializers.ValidationError({
                    'scheduled_at': (
                        f'El profesional ya tiene una cita activa que se solapa: '
                        f'{conflict} ({conflict.scheduled_at:%H:%M}–{conflict.get_end_datetime():%H:%M}).'
                    )
                })

        return attrs

    class Meta:
        model = Appointment
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'confirmation_token')
