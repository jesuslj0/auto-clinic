from django.contrib import admin

from agent.models import AgentMemory, ConversationSession, WorkflowError


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'created_at')
    search_fields = ('session_id',)


@admin.register(WorkflowError)
class WorkflowErrorAdmin(admin.ModelAdmin):
    list_display = ('workflow', 'node_name', 'phone', 'created_at')
    list_filter = ('workflow',)
    search_fields = ('workflow', 'workflow_name', 'phone', 'error_message')


@admin.register(ConversationSession)
class ConversationSessionAdmin(admin.ModelAdmin):
    list_display = ('phone', 'clinic', 'last_interaction', 'updated_at')
    search_fields = ('phone',)
