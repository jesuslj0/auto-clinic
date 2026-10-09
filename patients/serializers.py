from rest_framework import serializers

from core.serializers import ClinicScopedSerializerMixin
from patients.models import Patient
from patients.services import create_patient, normalize_phone


class PatientSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    # Declarado a mano y no con `extra_kwargs`: `phone` es `blank=True` en el modelo
    # (el familiar sin móvil se gestiona por un contacto) pero la API del agente lo
    # sigue exigiendo. DRF >= 3.18 le añade un `default` al campo por estar en una
    # restricción única con condición, y chocaba con `required=True` ("May not set
    # both `required` and `default`"): daba 500 al serializar cualquier paciente.
    phone = serializers.CharField(max_length=32, required=True, allow_blank=False)

    class Meta:
        model = Patient
        # `photo` fuera: la API es la puerta del agente (n8n) y la foto solo se
        # sirve por sesión, firmada y con `AccessLog` (`patients:photo`).
        exclude = ("photo",)
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
