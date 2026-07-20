from rest_framework import serializers

from core.authentication import ClinicAgent
from patients.models import Patient
from patients.services import create_patient, normalize_phone


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def _enforce_clinic(self, validated_data):
        """Impide el cruce entre clínicas: un usuario con clínica asignada
        siempre opera sobre la suya, aunque el payload traiga otro `clinic`.

        Solo el superusuario (o un usuario sin clínica) puede fijar la clínica
        libremente desde el payload. Cubre create, bulk-create y update, que
        es donde el serializer es el único punto de paso común.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None:
            return
        if isinstance(user, ClinicAgent):
            validated_data["clinic"] = user.clinic
        elif not user.is_superuser and getattr(user, "clinic_id", None):
            validated_data["clinic"] = user.clinic

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
