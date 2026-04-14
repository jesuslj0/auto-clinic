from rest_framework import serializers

from appointments.models import Professional
from core.models import Clinic, User
from services.models import Service


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    professional_id = serializers.IntegerField(source='professional_profile.id', read_only=True)
    professional_role = serializers.CharField(required=False, allow_blank=True)
    professional_services = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.all(), many=True, required=False
    )

    class Meta:
        model = User
        fields = (
            'id', 'email', 'password', 'first_name', 'last_name', 'clinic',
            'role', 'professional_id', 'professional_role', 'professional_services',
            'is_active', 'created_at', 'updated_at'
        )
        read_only_fields = ('created_at', 'updated_at')

    def create(self, validated_data):
        professional_role = validated_data.pop('professional_role', '')
        professional_services = validated_data.pop('professional_services', [])
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        user.save()
        self._sync_professional(user, professional_role, professional_services)
        return user

    def update(self, instance, validated_data):
        professional_role = validated_data.pop('professional_role', None)
        professional_services = validated_data.pop('professional_services', None)
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        self._sync_professional(instance, professional_role, professional_services)
        return instance

    def _sync_professional(self, user, professional_role, professional_services):
        if not user.clinic_id or not user.role:
            return

        professional, _ = Professional.objects.get_or_create(
            user=user,
            defaults={'clinic': user.clinic, 'role': professional_role or user.role},
        )

        should_save = False
        if professional.clinic_id != user.clinic_id:
            professional.clinic = user.clinic
            should_save = True
        if professional_role is not None and professional.role != professional_role:
            professional.role = professional_role
            should_save = True

        if should_save:
            professional.save()

        if professional_services is not None:
            professional.services.set(professional_services)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        clinic = attrs.get('clinic', getattr(self.instance, 'clinic', None))
        professional_services = attrs.get('professional_services')

        if professional_services and not clinic:
            raise serializers.ValidationError(
                {'professional_services': 'A clinic is required to assign professional services.'}
            )

        if professional_services and any(service.clinic_id != clinic.clinic_id for service in professional_services):
            raise serializers.ValidationError(
                {'professional_services': 'All selected services must belong to the selected clinic.'}
            )

        return attrs
