from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response


def is_clinic_admin(user):
    """¿Puede este usuario gestionar la configuración de la clínica y a los demás?

    Existe como función y no solo como mixin porque la respuesta hace falta
    también para *pintar* la interfaz: un botón que lleva a una vista prohibida
    es una trampa, así que la lista de profesionales pregunta lo mismo que
    después comprueba `ClinicAdminRequiredMixin`.
    """
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, 'role', None) == 'admin')
    )


class ClinicAdminRequiredMixin(LoginRequiredMixin):
    """Restringe el acceso a administradores de la clínica (o superusuarios).

    Vive aquí, y no en `core/views.py`, porque lo usan también las vistas de
    otras apps (la gestión de profesionales en `appointments`): importar
    `core.views` desde otra app arrastraría media aplicación por una clase de
    cinco líneas.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_clinic_admin(request.user):
            raise PermissionDenied('Solo los administradores pueden gestionar esta configuración.')
        return super().dispatch(request, *args, **kwargs)


class ExportMixin:
    """Adds GET /export/ — returns all records without pagination for n8n consumption."""

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class BulkCreateMixin:
    """Adds POST /bulk-create/ — inserts multiple records in one request."""

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BulkUpdateMixin:
    """Adds PATCH /bulk-update/ — partially updates multiple records by id."""

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        results = []
        errors = []
        for item in request.data:
            pk = item.get('id')
            try:
                instance = self.get_queryset().get(pk=pk)
            except ObjectDoesNotExist:
                errors.append({'id': str(pk), 'error': 'Not found'})
                continue
            serializer = self.get_serializer(instance, data=item, partial=True)
            if serializer.is_valid():
                serializer.save()
                results.append(serializer.data)
            else:
                errors.append({'id': str(pk), 'error': serializer.errors})
        return Response({'updated': results, 'errors': errors})


class N8nMixin(ExportMixin, BulkCreateMixin, BulkUpdateMixin):
    """Full n8n integration mixin: export + bulk create + bulk update."""
