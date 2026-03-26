from rest_framework import serializers

from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase


class ClinicKnowledgeBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicKnowledgeBase
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class ClinicInfoQuerySerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicInfoQuery
        fields = '__all__'
        read_only_fields = ('id', 'created_at')


class ClinicInfoCacheSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicInfoCache
        fields = '__all__'
        read_only_fields = ('id', 'created_at')
