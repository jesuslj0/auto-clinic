from django.contrib import admin

from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase


@admin.register(ClinicKnowledgeBase)
class ClinicKnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'kb_type', 'title', 'active', 'created_at')
    list_filter = ('kb_type', 'active')
    search_fields = ('title', 'content', 'clinic__name')


@admin.register(ClinicInfoQuery)
class ClinicInfoQueryAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'intent_category', 'created_at')
    list_filter = ('intent_category',)
    search_fields = ('question', 'answer')


@admin.register(ClinicInfoCache)
class ClinicInfoCacheAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'intent_category', 'created_at')
    list_filter = ('intent_category',)
    search_fields = ('normalized_question', 'answer')
