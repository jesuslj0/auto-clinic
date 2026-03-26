from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response


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
