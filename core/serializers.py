from rest_framework import serializers

from appointments.models import Professional
from core.models import Clinic, User


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


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
