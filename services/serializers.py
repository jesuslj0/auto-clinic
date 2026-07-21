from rest_framework import serializers

from core.serializers import ClinicScopedSerializerMixin
from services.models import Service


class ServiceSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')
