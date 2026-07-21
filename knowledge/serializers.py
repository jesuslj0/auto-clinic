from rest_framework import serializers

from core.serializers import ClinicScopedSerializerMixin
from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase


class ClinicKnowledgeBaseSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = ClinicKnowledgeBase
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class ClinicInfoQuerySerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = ClinicInfoQuery
        fields = '__all__'
        read_only_fields = ('id', 'created_at')


class ClinicInfoCacheSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = ClinicInfoCache
        fields = '__all__'
        read_only_fields = ('id', 'created_at')
