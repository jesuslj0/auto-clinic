"""Instrumentación de LECTURAS.

Las lecturas no emiten señales, así que hay que declararlas. Tres formas, de
menos a más manual:

- `AccessLogMixin`      — vistas basadas en clase (`DetailView`, `ListView`).
- `AuditedViewSetMixin` — viewsets de DRF (`retrieve`, `list`, `export`).
- `@log_access_view`    — vistas basadas en función.
- `log_access(...)`     — llamada directa, para todo lo demás (por ejemplo la
  futura vista que sirva adjuntos protegidos).

La instrumentación es explícita y por vista. Ver `audit/middleware.py` sobre por
qué no se hace globalmente.
"""
from __future__ import annotations

from functools import wraps

from audit.context import build_context, get_audit_context, resolve_actor


def log_access(
    *,
    action,
    patient=None,
    obj=None,
    result_count=None,
    reason=None,
    request=None,
    path=None,
    method=None,
):
    """Registra un acceso. Invocable desde cualquier sitio.

    Todo lo que no se pase explícitamente se toma del contexto de la petición.
    Fuera de una petición (comando, tarea de Celery) el registro se crea igual,
    con usuario nulo y origen `command`.
    """
    from django.contrib.contenttypes.models import ContentType

    from audit.models import AccessLog
    from audit.registry import default_patient_resolver
    from audit.writer import write_log

    context = get_audit_context()
    if request is not None and context.request is None:
        # El middleware no está instalado o estamos fuera de su alcance.
        context = build_context(request)

    user, user_repr, origin = resolve_actor(context)

    if patient is None and obj is not None:
        try:
            patient = default_patient_resolver(obj)
        except Exception:
            patient = None

    content_type = None
    object_id = ''
    object_repr = ''
    if obj is not None:
        content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        object_id = str(obj.pk or '')
        object_repr = str(obj)[:255]

    return write_log(
        AccessLog,
        user=user,
        user_repr=user_repr,
        patient=patient,
        content_type=content_type,
        object_id=object_id,
        object_repr=object_repr,
        action=action,
        path=(path if path is not None else context.path)[:500],
        method=(method if method is not None else context.method)[:10],
        result_count=result_count,
        reason=reason,
        ip=context.ip,
        user_agent=context.user_agent,
        origin=origin,
    )


# ---------------------------------------------------------------------------
# Vistas basadas en clase
# ---------------------------------------------------------------------------

class AccessLogMixin:
    """Registra la lectura de un `DetailView` o un `ListView`.

    Se pone ANTES de la vista en la lista de bases:

        class PatientDetailView(AccessLogMixin, LoginRequiredMixin, DetailView):
            ...

    La acción se deduce (`view` para un detalle, `list`/`search` para un
    listado); se puede forzar con `access_action`. Solo se registra si la
    respuesta es correcta: un 404 no es un acceso a datos.
    """

    access_action = None
    #: Parámetros de query string que convierten un listado en una búsqueda.
    access_search_params = ('q', 'search')

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        if response.status_code < 300:
            self.log_access_event(request, response)
        return response

    # -- puntos de extensión ------------------------------------------------

    def get_access_action(self):
        from audit.models import AccessLog

        if self.access_action:
            return self.access_action
        if getattr(self, 'object', None) is not None:
            return AccessLog.Action.VIEW
        if any(self.request.GET.get(param) for param in self.access_search_params):
            return AccessLog.Action.SEARCH
        return AccessLog.Action.LIST

    def get_access_object(self):
        return getattr(self, 'object', None)

    def get_access_patient(self):
        from audit.registry import default_patient_resolver

        obj = self.get_access_object()
        if obj is None:
            return None
        try:
            return default_patient_resolver(obj)
        except Exception:
            return None

    def get_access_result_count(self):
        object_list = getattr(self, 'object_list', None)
        if object_list is None:
            return None
        try:
            return len(object_list)
        except TypeError:
            return object_list.count()

    def get_access_reason(self):
        return None

    def log_access_event(self, request, response):
        return log_access(
            action=self.get_access_action(),
            patient=self.get_access_patient(),
            obj=self.get_access_object(),
            result_count=self.get_access_result_count(),
            reason=self.get_access_reason(),
            request=request,
        )


# ---------------------------------------------------------------------------
# Vistas basadas en función
# ---------------------------------------------------------------------------

def log_access_view(action, *, patient=None, obj=None, result_count=None):
    """Decorador equivalente a `AccessLogMixin` para FBVs.

    `patient`, `obj` y `result_count` admiten un valor fijo o un callable que
    recibe `(request, *args, **kwargs)`.

        @log_access_view(AccessLog.Action.DOWNLOAD_ATTACHMENT,
                         patient=lambda request, pk: Attachment.objects.get(pk=pk).patient)
        def download_attachment(request, pk):
            ...
    """

    def resolve(value, request, args, kwargs):
        return value(request, *args, **kwargs) if callable(value) else value

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            if getattr(response, 'status_code', 200) < 300:
                log_access(
                    action=action,
                    patient=resolve(patient, request, args, kwargs),
                    obj=resolve(obj, request, args, kwargs),
                    result_count=resolve(result_count, request, args, kwargs),
                    request=request,
                )
            return response

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Viewsets de DRF
# ---------------------------------------------------------------------------

class AuditedViewSetMixin:
    """Registra las lecturas de un `ModelViewSet`.

    Cubre `retrieve`, `list` (distinguiendo búsqueda) y el `export` de
    `core.mixins.ExportMixin`. Se pone antes que el resto de bases:

        class PatientViewSet(AuditedViewSetMixin, ExportMixin, viewsets.ModelViewSet):
            ...

    El enganche es `finalize_response` y no un override de cada método a
    propósito: sobrescribir `export()` le quitaría los metadatos que el decorador
    `@action` le puso y DRF dejaría de enrutarla.

    Las ESCRITURAS de este mismo viewset no hace falta declararlas: las capta
    `ChangeLog` por señales, siempre que el modelo esté registrado.
    """

    #: Parámetro de búsqueda de `rest_framework.filters.SearchFilter`.
    access_search_param = 'search'
    #: Acciones del viewset que cuentan como lectura auditable.
    access_logged_actions = ('retrieve', 'list', 'export')

    def get_object(self):
        obj = super().get_object()
        # Se cachea para no repetir la consulta al escribir el registro.
        self._audit_object = obj
        return obj

    def get_access_patient(self, obj):
        from audit.registry import default_patient_resolver

        if obj is None:
            return None
        try:
            return default_patient_resolver(obj)
        except Exception:
            return None

    def get_access_action(self, request):
        from audit.models import AccessLog

        if self.action == 'retrieve':
            return AccessLog.Action.VIEW
        if self.action == 'export':
            return AccessLog.Action.EXPORT
        if request.query_params.get(self.access_search_param):
            return AccessLog.Action.SEARCH
        return AccessLog.Action.LIST

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if getattr(self, 'action', None) in self.access_logged_actions and response.status_code < 300:
            obj = getattr(self, '_audit_object', None) if self.action == 'retrieve' else None
            log_access(
                action=self.get_access_action(request),
                obj=obj,
                patient=self.get_access_patient(obj),
                result_count=None if obj is not None else _response_count(response),
                request=request,
            )
        return response


def _response_count(response):
    """Número de filas devueltas, con o sin paginación."""
    data = getattr(response, 'data', None)
    if isinstance(data, dict) and 'count' in data:
        return data['count']
    try:
        return len(data)
    except TypeError:
        return None
