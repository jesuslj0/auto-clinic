"""Admin de la capa clínica.

Dos cosas más allá del alta normal de modelos:

1. **Instrumentación de lecturas.** El admin es una vista que muestra datos
   clínicos, así que registra `AccessLog`. `AuditedAdminMixin` engancha la vista
   de detalle (una ficha concreta → acción `view`, con su paciente) y el listado
   (acción `list`/`search`). Es la contrapartida de que esta capa no tenga API:
   la única forma de leerla es el admin, y queda trazada.
2. **Nota firmada en solo lectura.** Una nota firmada no se edita ni se borra
   desde el admin: todos sus campos pasan a `readonly` y se retira el permiso de
   borrado. Coherente con la barrera del modelo y el trigger de base de datos.
"""
from django.contrib import admin

from audit import registry
from audit.mixins import log_access
from audit.models import AccessLog

from clinical.models import (
    Addendum,
    ClinicalNote,
    Episode,
    MedicalHistory,
    Visit,
)


class AuditedAdminMixin:
    """Registra en `AccessLog` las lecturas del admin sobre un modelo clínico."""

    def _resolve_patient(self, obj):
        config = registry.get_config(self.model)
        if config is None or obj is None:
            return None
        return config.resolve_patient(obj)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        response = super().change_view(request, object_id, form_url, extra_context)
        if getattr(response, 'status_code', 200) < 400:
            obj = self.get_object(request, object_id)
            if obj is not None:
                log_access(
                    action=AccessLog.Action.VIEW,
                    obj=obj,
                    patient=self._resolve_patient(obj),
                    request=request,
                )
        return response

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        if getattr(response, 'status_code', 200) < 400:
            action = AccessLog.Action.SEARCH if request.GET.get('q') else AccessLog.Action.LIST
            log_access(action=action, request=request)
        return response


@admin.register(MedicalHistory)
class MedicalHistoryAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ('number', 'patient', 'clinic', 'opened_at')
    search_fields = ('number', 'patient__first_name', 'patient__last_name')
    list_filter = ('clinic',)
    readonly_fields = ('number', 'opened_at', 'deleted_at')
    raw_id_fields = ('patient',)

    def has_delete_permission(self, request, obj=None):
        # La historia no se borra nunca.
        return False


@admin.register(Episode)
class EpisodeAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'history', 'status', 'opened_at', 'discharged_at')
    list_filter = ('status',)
    search_fields = ('history__number',)
    raw_id_fields = ('history', 'responsible_professional')
    readonly_fields = ('deleted_at',)


@admin.register(Visit)
class VisitAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'episode', 'professional', 'occurred_at')
    search_fields = ('episode__history__number',)
    raw_id_fields = ('episode', 'professional', 'appointment')
    readonly_fields = ('deleted_at',)


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'visit', 'status', 'signed_by', 'signed_at')
    list_filter = ('status',)
    search_fields = ('visit__episode__history__number',)
    raw_id_fields = ('visit', 'signed_by')

    def _is_signed(self, obj):
        return obj is not None and obj.status == ClinicalNote.Status.SIGNED

    def get_readonly_fields(self, request, obj=None):
        if self._is_signed(obj):
            # Firmada = inmutable: todo en solo lectura.
            return [f.name for f in self.model._meta.fields]
        return ('signed_by', 'signed_at', 'content_hash', 'deleted_at')

    def has_delete_permission(self, request, obj=None):
        # Una nota firmada no se borra jamás.
        return not self._is_signed(obj)


@admin.register(Addendum)
class AddendumAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'note', 'author', 'created_at')
    search_fields = ('note__visit__episode__history__number',)
    raw_id_fields = ('note', 'author')

    def has_change_permission(self, request, obj=None):
        # De solo inserción: se puede crear y consultar, nunca editar.
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False
