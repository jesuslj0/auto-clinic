from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, IntegerField, Max, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView
from rest_framework import viewsets

from appointments.models import Appointment
from clinical.models import PerformedProcedure
from core.authentication import ClinicAgent
from core.mixins import ClinicAdminRequiredMixin, ExportMixin, is_clinic_admin
from core.permissions import IsAgentClinicKey, IsStaffOrAdmin
from services.forms import ServiceForm
from services.models import Service, ServiceCategory
from services.serializers import ServiceCategorySerializer, ServiceSerializer


class ServiceCategoryViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ServiceCategorySerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    search_fields = ['name']
    filterset_fields = ['clinic', 'is_active']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        queryset = ServiceCategory.objects.select_related('clinic')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ServiceViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = ServiceSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    search_fields = ['name', 'description']
    filterset_fields = ['clinic', 'is_active', 'category']
    ordering_fields = ['name', 'price', 'duration_minutes', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        queryset = Service.objects.select_related('clinic', 'category')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class ServiceListView(LoginRequiredMixin, ListView):
    model = Service
    template_name = 'services/service_list.html'
    context_object_name = 'services'
    # Activos primero: la plantilla pinta un separador donde empiezan los inactivos.
    ordering = ['-is_active', 'name']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        context['can_manage'] = is_clinic_admin(self.request.user)
        # Totales de los botones de filtro. Se cuentan sobre la lista ya cargada
        # (la plantilla la recorre igualmente), así que no cuestan otra consulta.
        activos = sum(1 for service in self.object_list if service.is_active)
        context['total_count'] = len(self.object_list)
        context['active_count'] = activos
        context['inactive_count'] = context['total_count'] - activos
        # Solo las categorías con servicios a la vista: el desplegable filtra lo
        # que hay en pantalla, no el catálogo de categorías completo.
        context['categories'] = (
            ServiceCategory.objects.filter(services__in=self.object_list).distinct()
        )
        return context

    def get_queryset(self):
        return (
            _services_for(self.request.user)
            .select_related('clinic', 'category')
            .annotate(
                appointments_count=_count_per_service(Appointment.objects.all()),
                procedures_count=_count_per_service(PerformedProcedure.objects.all()),
            )
            .order_by(*self.ordering)
        )


class ServiceCreateView(LoginRequiredMixin, CreateView):
    model = Service
    form_class = ServiceForm
    template_name = 'services/service_form.html'
    success_url = reverse_lazy('services:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['clinic'] = self.request.user.clinic
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada. Contacta con el administrador.')
            return redirect('services:list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.clinic = self.request.user.clinic
        messages.success(self.request, 'Servicio creado correctamente.')
        return super().form_valid(form)


class ServiceUpdateView(ClinicAdminRequiredMixin, UpdateView):
    model = Service
    form_class = ServiceForm
    template_name = 'services/service_form.html'
    success_url = reverse_lazy('services:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'services'
        context['stats'] = _usage_stats(self.object)
        context['appointments_count'] = context['stats']['appointments']['total']
        return context

    def get_queryset(self):
        return _services_for(self.request.user).select_related('clinic', 'category')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['clinic'] = self.object.clinic
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Servicio actualizado correctamente.')
        return super().form_valid(form)


class ServiceDeleteView(ClinicAdminRequiredMixin, View):
    """Borra un servicio del catálogo desde el modal de su ficha.

    El veto vive en el modelo (`can_be_deleted()`); aquí solo se traduce a un
    mensaje y se devuelve a la ficha, donde la alternativa es desactivarlo.
    """

    http_method_names = ['post']

    def post(self, request, pk):
        service = get_object_or_404(_services_for(request.user), pk=pk)
        if not service.can_be_deleted():
            messages.error(
                request,
                'Este servicio tiene citas y no se puede eliminar. '
                'Desactívalo para que deje de ofrecerse.',
            )
            return redirect('services:edit', pk=service.pk)

        nombre = service.name
        service.delete()
        messages.success(request, f'Servicio «{nombre}» eliminado.')
        return redirect('services:list')


def _services_for(user):
    """Servicios que el usuario puede tocar: los de su clínica, o todos si no tiene."""
    if not user.clinic_id:
        return Service.objects.all()
    return Service.objects.filter(clinic=user.clinic)


def _count_per_service(queryset):
    """Cuántas filas de `queryset` cuelgan de cada servicio, como subconsulta.

    Subconsulta y no `Count()` sobre la relación inversa: con dos `Count` en la
    misma consulta los JOIN se multiplican entre sí (citas × procedimientos) y
    ambos números salen inflados. Así cada recuento es un `SELECT COUNT(*)`
    agrupado por `service_id` que usa su índice, y todo va en una sola consulta.
    El `queryset` llega con su manager por defecto, de modo que los
    procedimientos dados de baja (soft-delete) no cuentan.
    """
    counts = (
        queryset.filter(service=OuterRef('pk'))
        .order_by()
        .values('service')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return Coalesce(Subquery(counts, output_field=IntegerField()), 0)


def _usage_stats(service):
    """Resumen de uso de un servicio para su ficha: una consulta por modelo.

    Cada bloque es un único `aggregate()` con recuentos condicionales
    (`COUNT(*) FILTER (WHERE …)` en PostgreSQL), no una consulta por cifra.
    Solo cifras agregadas: ni pacientes ni contenido clínico.
    """
    St = Appointment.Status
    # Desglose por estado, uno por cada valor de `Status`: las filas son
    # excluyentes y exhaustivas, así que siempre suman el total. «Próximas» es
    # otro eje (fecha), y va aparte: mezclarlo con los estados dejaba fuera las
    # citas pendientes o confirmadas con fecha ya pasada.
    appointments = Appointment.objects.filter(service=service).aggregate(
        total=Count('pk'),
        upcoming=Count('pk', filter=Q(
            scheduled_at__gte=timezone.now(), status__in=[St.PENDING, St.CONFIRMED],
        )),
        **{status: Count('pk', filter=Q(status=status)) for status in St.values},
    )
    appointments['by_status'] = [
        {'status': status, 'label': label, 'count': appointments.pop(status)}
        for status, label in St.choices
    ]
    # Manager por defecto: los procedimientos dados de baja no cuentan.
    procedures = PerformedProcedure.objects.filter(service=service).aggregate(
        total=Count('pk'),
        # Mismo criterio que facturación: sin factura = pendiente (anular una
        # factura suelta sus procedimientos).
        pending_invoice=Count('pk', filter=Q(invoice__isnull=True)),
        amount=Coalesce(Sum('frozen_price'), Decimal('0')),
        last_performed=Max('performed_at'),
    )
    return {'appointments': appointments, 'procedures': procedures}
