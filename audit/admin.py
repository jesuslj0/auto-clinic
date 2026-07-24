"""Los dos registros en el admin, en modo estrictamente de solo lectura.

Se puede consultar y filtrar, nunca crear, editar ni borrar. El objetivo es que
el admin sirva para responder «quién tocó la ficha de este paciente» sin ser a
la vez la herramienta con la que alguien podría reescribir esa respuesta.
"""
from django.contrib import admin

from audit.models import AccessLog, ChangeLog


class ReadOnlyLogAdmin(admin.ModelAdmin):
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)
    list_select_related = ('user', 'patient', 'content_type')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(ChangeLog)
class ChangeLogAdmin(ReadOnlyLogAdmin):
    list_display = ('timestamp', 'action', 'model_label', 'object_repr', 'user_repr', 'origin')
    list_filter = ('action', 'origin', 'model_label', 'timestamp')
    search_fields = (
        'user_repr',
        'object_repr',
        'object_id',
        'model_label',
        'patient__first_name',
        'patient__last_name',
    )
    raw_id_fields = ('user', 'patient')


@admin.register(AccessLog)
class AccessLogAdmin(ReadOnlyLogAdmin):
    list_display = ('timestamp', 'action', 'user_repr', 'patient', 'path', 'result_count', 'origin')
    list_filter = ('action', 'origin', 'method', 'timestamp')
    search_fields = (
        'user_repr',
        'path',
        'object_repr',
        'patient__first_name',
        'patient__last_name',
    )
    raw_id_fields = ('user', 'patient')
