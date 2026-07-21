from django.contrib import admin

from agent.models import AgentMemory, ChatMessage, ConversationSession, WorkflowError


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'clinic', 'created_at')
    list_filter = ('clinic',)
    search_fields = ('session_id',)


@admin.register(WorkflowError)
class WorkflowErrorAdmin(admin.ModelAdmin):
    list_display = ('workflow', 'clinic', 'node_name', 'phone', 'created_at')
    list_filter = ('clinic', 'workflow')
    search_fields = ('workflow', 'workflow_name', 'phone', 'error_message')


@admin.register(ConversationSession)
class ConversationSessionAdmin(admin.ModelAdmin):
    list_display = (
        'phone', 'clinic', 'patient', 'unread_count', 'agent_paused', 'last_message_at'
    )
    list_filter = ('clinic', 'agent_paused')
    search_fields = ('phone',)
    readonly_fields = ('last_message_at', 'last_message_preview', 'unread_count')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'clinic', 'session', 'sender', 'message_type', 'status')
    list_filter = ('clinic', 'direction', 'sender', 'message_type', 'status')
    search_fields = ('body', 'wa_message_id', 'session__phone')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
