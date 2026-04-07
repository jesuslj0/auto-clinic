from rest_framework import serializers

from patients.models import Patient
from patients.services import normalize_phone


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

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
