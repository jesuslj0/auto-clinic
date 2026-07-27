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
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    TemplateVersion,
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


# ---------------------------------------------------------------------------
# Anamnesis
# ---------------------------------------------------------------------------
#
# El admin es la tercera barrera de la inmutabilidad (modelo, trigger y aquí):
# una versión publicada se muestra en solo lectura, sin borrar y sin poder tocar
# sus preguntas. Si se colara un cambio, el `save()` del modelo lo rechazaría con
# una excepción fea; esto es lo que hace que el admin ni lo ofrezca.


@admin.register(QuestionnaireTemplate)
class QuestionnaireTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'clinic', 'specialty', 'is_active', 'current_version')
    list_filter = ('clinic', 'is_active')
    search_fields = ('name', 'specialty')
    readonly_fields = ('deleted_at',)

    @admin.display(description='versión vigente')
    def current_version(self, obj):
        version = obj.current_version
        return version.number if version else '—'


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1
    fields = ('order', 'text', 'answer_type', 'is_required', 'options')
    ordering = ('order', 'id')

    def _version_is_published(self, obj):
        return obj is not None and obj.is_published

    def get_readonly_fields(self, request, obj=None):
        if self._version_is_published(obj):
            return self.fields
        return ()

    def has_add_permission(self, request, obj=None):
        return not self._version_is_published(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._version_is_published(obj)


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'template', 'number', 'is_published', 'is_current', 'published_at')
    list_filter = ('is_published', 'is_current', 'template')
    search_fields = ('template__name',)
    raw_id_fields = ('template',)
    inlines = [QuestionInline]
    actions = ['publish_versions']

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_published:
            # Publicada = congelada. `is_current` sigue siendo editable porque es
            # ciclo de vida, no contenido: así se puede cambiar la vigente.
            return ('template', 'number', 'is_published', 'published_at', 'deleted_at')
        return ('number', 'is_published', 'published_at', 'deleted_at')

    def has_delete_permission(self, request, obj=None):
        # Una versión publicada pudo haber sido respondida: no se borra.
        return not (obj is not None and obj.is_published)

    @admin.action(description='Publicar las versiones seleccionadas')
    def publish_versions(self, request, queryset):
        published = 0
        for version in queryset:
            if version.is_published:
                continue
            try:
                version.publish()
            except Exception as exc:  # noqa: BLE001 — el motivo se muestra al usuario
                self.message_user(request, f'{version}: {exc}', level='error')
            else:
                published += 1
        if published:
            self.message_user(request, f'{published} versión(es) publicada(s).')


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'version', 'order', 'answer_type', 'is_required')
    list_filter = ('answer_type', 'is_required')
    search_fields = ('text',)
    raw_id_fields = ('version',)
    readonly_fields = ('deleted_at',)

    def _is_frozen(self, obj):
        return obj is not None and obj.version.is_published

    def get_readonly_fields(self, request, obj=None):
        if self._is_frozen(obj):
            return [f.name for f in self.model._meta.fields]
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return not self._is_frozen(obj)


@admin.register(QuestionnaireResponse)
class QuestionnaireResponseAdmin(AuditedAdminMixin, admin.ModelAdmin):
    """Solo consulta: una respuesta se registra desde su canal, nunca a mano.

    Muestra dato clínico (el snapshot), así que instrumenta `AccessLog` como el
    resto de la capa.
    """

    list_display = ('__str__', 'patient', 'episode', 'version', 'source', 'created_by', 'filled_at')
    list_filter = ('source', 'version__template')
    search_fields = ('patient__first_name', 'patient__last_name')
    raw_id_fields = ('version', 'patient', 'episode', 'created_by')
    date_hierarchy = 'filled_at'

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
