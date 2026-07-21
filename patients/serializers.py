from rest_framework import serializers

from core.serializers import ClinicScopedSerializerMixin
from patients.models import Patient
from patients.services import create_patient, normalize_phone


class PatientSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        # La creación real (normalización de teléfono) vive en el service
        # compartido por API y panel web.
        self._enforce_clinic(validated_data)
        return create_patient(**validated_data)

    def update(self, instance, validated_data):
        self._enforce_clinic(validated_data)
        return super().update(instance, validated_data)

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(
                {
                    "code": "INVALID_PHONE",
                    "message": str(exc),
                    "details": {},
                }
            ) from exc
