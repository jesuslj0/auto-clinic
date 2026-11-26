"""Vistas de las exportaciones de datos (panel, sesión, solo administradores).

Nada de esto es API: el agente (`Api-Key`) no llega aquí y no hay ruta REST. Toda
exportación y toda descarga dejan `AccessLog` (acción `EXPORT`).
"""
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import ListView

from audit.mixins import log_access
from audit.models import AccessLog
from core import two_factor
from core.mixins import ClinicAdminRequiredMixin
from core.reauth import reauthenticate
from exports.models import DataExport, retention_days
from exports.service import ExportAlreadyRunning, request_export
from patients.models import Patient


class ExportAdminMixin(ClinicAdminRequiredMixin):
    """Administrador de una clínica. Sin clínica no hay nada que exportar."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(request.user, 'clinic_id', None) is None:
            # Un superusuario de la plataforma sin clínica no exporta la de nadie.
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied('Tu usuario no pertenece a ninguna clínica.')
        return super().dispatch(request, *args, **kwargs)


class ExportListView(ExportAdminMixin, ListView):
    """Historial de exportaciones de la clínica y estado de las que están en marcha.

    No lee contenido clínico (solo quién pidió qué y cuándo), así que no escribe
    `AccessLog`: las peticiones y las descargas, que sí tocan datos, lo escriben
    cada una. Con `?fragment=1` devuelve solo las filas, para el refresco.
    """

    model = DataExport
    context_object_name = 'exports'
    paginate_by = 30

    def get_queryset(self):
        return DataExport.objects.filter(clinic=self.request.user.clinic).select_related('requested_by')

    def get_template_names(self):
        if self.request.GET.get('fragment'):
            return ['exports/_rows.html']
        return ['exports/list.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'exports'
        context['has_active'] = DataExport.objects.filter(
            clinic=self.request.user.clinic, status__in=DataExport.ACTIVE_STATUSES
        ).exists()
        context['retention_days'] = retention_days()
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class PatientExportCreateView(ExportAdminMixin, View):
    """Pide la exportación de la ficha de un paciente (POST desde su ficha)."""

    http_method_names = ['post']

    def post(self, request, patient_id):
        # Los archivados también se exportan: pueden pedir su copia.
        patient = get_object_or_404(Patient, pk=patient_id, clinic=request.user.clinic)
        try:
            request_export(clinic=request.user.clinic, user=request.user, patient=patient)
        except ExportAlreadyRunning:
            messages.warning(request, f'Ya hay una exportación de {patient} en marcha.')
        else:
            messages.success(
                request,
                f'Preparando la exportación de {patient}. Te avisaremos cuando esté lista.',
            )
        return redirect('exports:list')


class ExportDownloadView(ExportAdminMixin, View):
    """Redirige al ZIP, en una URL firmada que caduca en minutos.

    Django nunca sirve el fichero: lo entrega el bucket. Se comprueba la clínica
    (otra clínica es un 404), que esté lista y dentro de plazo, y se registra la
    descarga antes de redirigir.
    """

    def get(self, request, pk):
        export = get_object_or_404(DataExport, pk=pk, clinic=request.user.clinic)
        if not export.is_downloadable:
            raise Http404('Esta exportación no está disponible.')

        log_access(
            action=AccessLog.Action.EXPORT,
            patient=export.patient,
            description=(
                'Descarga de exportación de un paciente'
                if export.scope == DataExport.Scope.PATIENT else 'Descarga de exportación de la clínica'
            ),
        )
        export.downloads += 1
        export.save(update_fields=['downloads', 'updated_at'])

        response = HttpResponseRedirect(export.file.url)
        # La redirección lleva una URL firmada: ni el navegador ni un proxy deben quedársela.
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Referrer-Policy'] = 'no-referrer'
        return response


@method_decorator([never_cache, sensitive_post_parameters('password', 'code')], name='dispatch')
class ClinicExportCreateView(ExportAdminMixin, View):
    """Pide la exportación de TODA la clínica.

    Es el volcado más grande y más sensible que hace la aplicación, así que pide
    contraseña y código de la verificación en dos pasos otra vez, con el mismo
    bloqueo por intentos que el login (`core.reauth`). Y **exige tenerla activa**:
    sin segundo factor no hay con qué confirmar que quien pide el volcado es quien
    dice (la contraseña sola es justo lo que un móvil prestado ya tiene).
    """

    template_name = 'exports/clinic_form.html'

    def _render(self, request, status=200):
        response = render(request, self.template_name, {
            'section': 'exports',
            'has_two_factor': two_factor.has_confirmed_device(request.user),
            'retention_days': retention_days(),
            'running': DataExport.objects.filter(
                clinic=request.user.clinic, scope=DataExport.Scope.CLINIC,
                status__in=DataExport.ACTIVE_STATUSES,
            ).exists(),
        }, status=status)
        # Mismas cabeceras que las páginas de seguridad: `same-origin`, NUNCA
        # `no-referrer` (el navegador mandaría `Origin: null` y CSRF rechazaría el POST).
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Referrer-Policy'] = 'same-origin'
        return response

    def get(self, request):
        return self._render(request)

    def post(self, request):
        if not two_factor.has_confirmed_device(request.user):
            return self._render(request, status=403)
        if not reauthenticate(request):
            return self._render(request)
        try:
            request_export(clinic=request.user.clinic, user=request.user)
        except ExportAlreadyRunning:
            messages.warning(request, 'Ya hay una exportación de la clínica en marcha.')
        else:
            messages.success(request, 'Preparando la exportación de la clínica. Te avisaremos cuando esté lista.')
        return redirect('exports:list')
