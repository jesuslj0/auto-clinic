"""Vistas de la capa clínica: servir un fichero protegido, y nada más.

Esta capa **no tiene API REST** a propósito (ver `clinical/README.md`), y estos
endpoints no la abren: son vistas de sesión del panel, no recursos de DRF, y no
devuelven dato clínico alguno más allá de una redirección al fichero pedido. El
token `Api-Key` del agente no autentica aquí, y aunque autenticara,
`can_view_patient()` lo rechaza explícitamente.
"""
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from appointments.filters import UNRECORDED_WINDOW_DAYS, completed_without_procedure
from appointments.models import Appointment, Professional
from audit.mixins import AccessLogMixin
from billing.filters import scope_to_clinic
from clinical.attachments import log_attachment_download, signed_url_for
from clinical.models import LesionAttachment, SignedConsent
from clinical.procedure_list import (
    BILLING_CHOICES,
    ProcedureFilters,
    procedure_totals,
    procedures_for,
)
from patients.models import Patient
from services.models import Service


class ProtectedFileRedirectView(LoginRequiredMixin, View):
    """Redirige a la URL firmada de un fichero clínico, tras comprobar el permiso.

    El orden importa y es el único posible: se comprueba quién pide
    (`signed_url_for` lanza `PermissionDenied` → 403), se registra el acceso en
    `AccessLog` y solo entonces se redirige. Django nunca sirve el fichero: lo
    entrega el bucket, contra una URL firmada que caduca en minutos.

    Se identifica por `public_id` (un UUID) y no por la PK: un id secuencial
    invita a tantear el de al lado, y aunque el 403 lo pararía, la existencia de
    la fila es de por sí información sobre un paciente.

    Las subclases solo dicen QUÉ se busca. El control —comprobar, registrar,
    redirigir— vive aquí una sola vez: repartido por vistas, es cuestión de
    tiempo que una se deje un paso.
    """

    def get_queryset(self):
        raise NotImplementedError

    def get(self, request, public_id):
        document = get_object_or_404(self.get_queryset(), public_id=public_id)
        url = signed_url_for(document, request.user)
        log_attachment_download(document, request=request)

        response = HttpResponseRedirect(url)
        # La redirección lleva una URL firmada: ni el navegador ni un proxy
        # intermedio deben quedársela.
        response['Cache-Control'] = 'private, no-store, max-age=0'
        return response


class LesionAttachmentDownloadView(ProtectedFileRedirectView):
    """Foto clínica de una observación de lesión."""

    def get_queryset(self):
        return LesionAttachment.objects.select_related(
            'observation__lesion__episode__history'
        )


class SignedConsentSignatureView(ProtectedFileRedirectView):
    """Firma manuscrita de un consentimiento.

    Una firma es dato personal pegado a un dato de salud, así que se sirve
    exactamente igual que una foto clínica: bucket privado, URL firmada de vida
    corta, permiso comprobado y `AccessLog`.
    """

    def get_queryset(self):
        return SignedConsent.objects.select_related('patient')



class ProcedureListView(AccessLogMixin, LoginRequiredMixin, ListView):
    """Listado global de procedimientos: todo lo que se ha hecho en la clínica.

    Es la vista transversal de lo que la ficha de cada paciente ya enseña por
    separado. Sirve para ver qué se hizo en un periodo, quién lo hizo y qué falta
    por facturar, sin entrar paciente a paciente.

    **Lee datos clínicos, así que registra `AccessLog`** (`AccessLogMixin`: un
    listado, o una búsqueda cuando llega `q`), a diferencia del listado de citas,
    que solo enseña información de agenda. Es una vista de sesión del panel, sin
    API y vedada al `Api-Key` del agente como el resto de la capa.

    Lo que se pinta —y se suma— es `frozen_service_name` y `frozen_price`: el
    nombre y el importe del día en que se hizo, nunca el catálogo. Por eso
    `service` no entra en el `select_related`.

    Encima de la tabla, el aviso de las citas completadas que no acabaron en
    ningún procedimiento: trabajo hecho que no se ha anotado y por tanto no se
    puede facturar.
    """

    template_name = 'clinical/procedure_list.html'
    context_object_name = 'procedures'
    paginate_by = 20

    def get_queryset(self):
        self.filters = ProcedureFilters.from_query(self.request.GET)
        self.filtered = self.filters.apply(procedures_for(self.request.user))
        queryset = self.filtered.select_related(
            'visit__episode__history__patient', 'visit__appointment',
            'created_by__user', 'invoice',
        )
        return self.filters.order(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        filters = self.filters

        professionals = scope_to_clinic(
            Professional.objects.select_related('user'), user
        ).order_by('user__first_name', 'user__last_name')
        services = scope_to_clinic(Service.objects.all(), user).order_by('name')

        # Aviso: citas completadas sin procedimiento anotado.
        since = timezone.now() - timedelta(days=UNRECORDED_WINDOW_DAYS)
        unrecorded = completed_without_procedure(
            scope_to_clinic(Appointment.objects.all(), user), since=since
        ).count()
        unrecorded_url = (
            f"{reverse('appointments:list')}?"
            + urlencode({
                'status': 'completed', 'procedimiento': 'sin', 'desde': since.date().isoformat(),
                'hasta': '', 'profesional': '', 'sort': 'desc',
            })
        )

        # Paciente fijado desde su ficha: se enseña su nombre para poder quitarlo.
        # Sale de los procedimientos que el usuario ya puede ver, no de `Patient`.
        patient = None
        if filters.patient:
            first = procedures_for(user).filter(
                visit__episode__history__patient_id=filters.patient
            ).select_related('visit__episode__history__patient').first()
            patient = first.visit.episode.history.patient if first else None
        clear_patient_query = urlencode({
            key: value for key, value in filters.as_params().items() if key != 'paciente'
        })

        params = filters.as_params()
        context.update({
            'filter_patient': patient,
            'clear_patient_query': clear_patient_query,
            'section': 'procedures',
            'filters': filters,
            'totals': procedure_totals(self.filtered),
            'professionals': professionals,
            'services': services,
            'billing_choices': BILLING_CHOICES,
            'query_base': urlencode(params),
            'sort_query': urlencode(filters.toggled().as_params()),
            'unrecorded_count': unrecorded,
            'unrecorded_days': UNRECORDED_WINDOW_DAYS,
            'unrecorded_url': unrecorded_url,
        })
        return context


class ProcedurePatientPickerView(AccessLogMixin, LoginRequiredMixin, ListView):
    """Primer paso de «Nuevo procedimiento» desde el listado global.

    Un procedimiento se registra siempre sobre un paciente, y el formulario
    (`patients:procedure-create`) ya existe y ya sabe de episodios, visitas e
    importes congelados: aquí solo se elige a quién. Cada resultado enlaza a ese
    formulario, así que no hay una segunda puerta de alta que mantener.

    Lista pacientes de la clínica del usuario, con búsqueda por nombre, apellidos
    o teléfono. Registra `AccessLog` (un listado, o una búsqueda si llega `q`)
    como el directorio de pacientes. No se ofrece a quien no tenga clínica: el
    paciente tiene que ser de alguna.
    """

    template_name = 'clinical/procedure_patient_picker.html'
    context_object_name = 'patients'
    paginate_by = 10

    def get_queryset(self):
        self.q = self.request.GET.get('q', '').strip()[:100]
        queryset = scope_to_clinic(Patient.objects.all(), self.request.user)
        for word in self.q.split():
            queryset = queryset.filter(
                Q(first_name__icontains=word)
                | Q(last_name__icontains=word)
                | Q(phone__icontains=word)
            )
        return queryset.order_by('last_name', 'first_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'section': 'procedures',
            'q': self.q,
            'query_base': urlencode({'q': self.q}) if self.q else '',
        })
        return context
