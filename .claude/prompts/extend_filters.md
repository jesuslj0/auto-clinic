## FASE 1 — AMPLIAR FILTROS EN DJANGO

### Problema actual

El AppointmentViewSet tiene filterset_fields = ['clinic', 'status', 'service', 'patient']. Los workflows necesitan filtrar por campos que el ViewSet actual no expone. Lo mismo pasa con otros ViewSets.

### Cambios necesarios en appointments/views.py

Crea un AppointmentFilter con django-filter (ya instalado en el proyecto) que añada:

python
import django_filters
from appointments.models import Appointment

class AppointmentFilter(django_filters.FilterSet):
    # Filtros que ya existen como filterset_fields — mantenerlos
    clinic = django_filters.UUIDFilter(field_name='clinic_id')  # o el tipo que corresponda
    status = django_filters.CharFilter(field_name='status')
    service = django_filters.UUIDFilter(field_name='service_id')
    patient = django_filters.UUIDFilter(field_name='patient_id')
    
    # NUEVOS — necesarios para n8n
    patient_phone = django_filters.CharFilter(field_name='patient_phone', lookup_expr='exact')
    reminder_24h_sent = django_filters.BooleanFilter(field_name='reminder_24h_sent')
    reminder_3h_sent = django_filters.BooleanFilter(field_name='reminder_3h_sent')
    reminder_responded = django_filters.BooleanFilter(field_name='reminder_responded')
    scheduled_at_gte = django_filters.IsoDateTimeFilter(field_name='scheduled_at', lookup_expr='gte')
    scheduled_at_lte = django_filters.IsoDateTimeFilter(field_name='scheduled_at', lookup_expr='lte')
    
    class Meta:
        model = Appointment
        fields = ['clinic', 'status', 'service', 'patient', 'patient_phone',
                  'reminder_24h_sent', 'reminder_3h_sent', 'reminder_responded']


Luego en el ViewSet, reemplaza filterset_fields por filterset_class = AppointmentFilter.

### Cambios necesarios en agent/views.py

El WorkflowErrorViewSet actualmente es ReadOnlyModelViewSet. Cámbialo a ModelViewSet para que n8n pueda hacer POST de errores:

python
class WorkflowErrorViewSet(viewsets.ModelViewSet):  # era ReadOnlyModelViewSet
    ...


El ConversationSessionViewSet ya soporta BulkCreate y BulkUpdate — verificar que también soporta PATCH individual y DELETE.

### Cambios necesarios en patients/views.py

Añadir phone a filterset_fields para que n8n pueda buscar pacientes por teléfono:

python
class PatientViewSet(viewsets.ModelViewSet):
    filterset_fields = ['clinic', 'phone']  # antes solo ['clinic']


### Cambios necesarios en knowledge/views.py (si existe)

Verificar que ClinicInfoCacheViewSet permite filtrar por normalized_question y clinic. Si no, añadir los filtros.

### Verificación

Después de hacer estos cambios:
1. Ejecuta python manage.py check para verificar que no hay errores
2. Ejecuta los tests existentes: pytest
3. Verifica en /api/docs/ (Swagger) que los nuevos filtros aparecen