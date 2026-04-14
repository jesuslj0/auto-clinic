from rest_framework import serializers

from appointments.models import Appointment


class AppointmentSerializer(serializers.ModelSerializer):
    patient_phone = serializers.CharField(source='patient.phone', read_only=True, default='')
    patient_name = serializers.SerializerMethodField()
    service_name = serializers.CharField(source='service.name', read_only=True, default='')

    def get_patient_name(self, obj):
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}".strip()
        return ''

    def validate(self, attrs):
        attrs = super().validate(attrs)
        professional = attrs.get('professional', getattr(self.instance, 'professional', None))
        service = attrs.get('service', getattr(self.instance, 'service', None))

        if professional and service and not professional.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError(
                {'professional': 'The selected professional does not provide the selected service.'}
            )
        return attrs

    class Meta:
        model = Appointment
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'confirmation_token')
