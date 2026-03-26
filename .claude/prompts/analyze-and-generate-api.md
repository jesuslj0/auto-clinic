Analiza este proyecto Django y genera/corrige todos los endpoints necesarios para integrarlo con n8n.

## FASE 1 — ANÁLISIS DEL PROYECTO

Lee y comprende la estructura actual:
1. Busca todos los modelos en `**/models.py` y `**/models/`
2. Busca todos los ViewSets en `**/views.py`, `**/views/`, `**/viewsets.py`
3. Busca los routers y URLs en `**/urls.py`
4. Lee `settings.py` para entender la configuración de DRF, CORS y autenticación
5. Busca serializers en `**/serializers.py`

Haz un resumen de lo que has encontrado antes de continuar.

## FASE 2 — AUDITORÍA Y CORRECCIÓN DE VIEWSETS

Para cada ViewSet existente, verifica y corrige si es necesario:

- Hereda de `ModelViewSet` (o el tipo adecuado: `ReadOnlyModelViewSet`, etc.)
- Tiene `queryset`, `serializer_class` y `permission_classes` definidos
- Tiene `filter_backends`, `search_fields` y `ordering_fields` configurados
- Los métodos HTTP habilitados son los correctos (`http_method_names` o `http_method_not_allowed`)
- Tiene paginación configurada si devuelve listas grandes
- Los permisos son coherentes con la lógica de negocio

Si faltan ViewSets para algún modelo relevante, créalos.

## FASE 3 — SERIALIZERS

Para cada modelo, verifica que exista un serializer adecuado:

- Usa `ModelSerializer` como base
- Incluye los campos necesarios para n8n (evita campos binarios o innecesarios)
- Añade un serializer de escritura y uno de lectura si la lógica lo requiere
- Valida que los campos `related` usen `PrimaryKeyRelatedField` o representación anidada según corresponda

## FASE 4 — ENDPOINTS PARA N8N

Genera o corrige los siguientes endpoints en cada app relevante:

### Endpoints estándar REST (vía router)
GET    /api/{recurso}/          → listar (con filtros, búsqueda, paginación)
POST   /api/{recurso}/          → crear
GET    /api/{recurso}/{id}/     → detalle
PUT    /api/{recurso}/{id}/     → actualizar completo
PATCH  /api/{recurso}/{id}/     → actualizar parcial
DELETE /api/{recurso}/{id}/     → eliminar

### Endpoints extra para n8n
Añade estas acciones personalizadas con `@action`:
```python
# Bulk create — para insertar múltiples registros desde n8n
@action(detail=False, methods=['post'], url_path='bulk-create')
def bulk_create(self, request): ...

# Bulk update — para actualizar múltiples registros
@action(detail=False, methods=['patch'], url_path='bulk-update')
def bulk_update(self, request): ...

# Export JSON — para que n8n consuma todos los datos paginados
@action(detail=False, methods=['get'], url_path='export')
def export(self, request): ...

# Status/health del recurso — útil para workflows condicionales en n8n
@action(detail=True, methods=['get'], url_path='status')
def status(self, request, pk=None): ...
```

## FASE 5 — AUTENTICACIÓN COMPATIBLE CON N8N

Verifica y configura en `settings.py`:
```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',  # para n8n Header Auth
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
}
```

Verifica que `rest_framework.authtoken` esté en `INSTALLED_APPS` y que exista el endpoint:
POST /api/auth/token/   → obtener token con usuario y contraseña

## FASE 6 — CORS PARA N8N

Asegura que `django-cors-headers` esté configurado:
```python
CORS_ALLOWED_ORIGINS = [
    "https://tu-instancia-n8n.com",  # ajustar
]
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["authorization", "content-type"]
```

## FASE 7 — URLS CENTRALES

Genera o actualiza el `urls.py` principal con un router centralizado que registre todos los ViewSets:
```python
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'recurso-a', RecursoAViewSet)
router.register(r'recurso-b', RecursoBViewSet)
# ... todos los recursos

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/auth/', include('rest_framework.urls')),
]
```

## FASE 8 — DOCUMENTACIÓN AUTOMÁTICA

Añade Swagger/OpenAPI para que n8n pueda explorar los endpoints:
```python
# En settings.py → INSTALLED_APPS
'drf_spectacular',

# En urls.py
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerUI
path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
path('api/docs/', SpectacularSwaggerUI.as_view(url_name='schema'), name='swagger-ui'),
```

## FASE 9 — INFORME FINAL

Al terminar, genera un resumen con:

1. **Lista de todos los endpoints disponibles** con método HTTP y URL
2. **Cambios realizados** sobre el código existente (qué se corrigió y por qué)
3. **Dependencias necesarias** (`pip install ...`) si se añadieron librerías
4. **Cómo configurar n8n** para conectarse: tipo de autenticación, URL base, cómo obtener el token
5. **Migraciones pendientes** si se modificaron modelos

---

Empieza por la Fase 1 y no saltes a la siguiente hasta completar la anterior.