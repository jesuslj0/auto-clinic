from rest_framework import serializers

from agent.models import AgentMemory, ConversationSession, WorkflowError


class AgentMemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMemory
        fields = '__all__'
        read_only_fields = ('id', 'created_at')


class WorkflowErrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowError
        fields = '__all__'
        read_only_fields = ('id', 'created_at')


class ConversationSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationSession
        fields = '__all__'
        read_only_fields = ('id', 'updated_at')
