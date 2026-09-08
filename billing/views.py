"""Vistas de facturación: la API de suscripciones y el panel de facturas."""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView
from django.views.generic.detail import SingleObjectMixin
from rest_framework import viewsets

from audit.mixins import AccessLogMixin
from audit.models import AccessLog
from billing.exceptions import (
    EmptyInvoice,
    InvoiceFrozen,
    InvoiceHasPayments,
    InvoiceNotDraft,
    InvoiceNotIssued,
    InvoiceNotPayable,
    Overpayment,
    PaymentFrozen,
)
from billing.filters import (
    InvoiceFilters,
    invoice_kpis,
    invoices_for,
    pending_procedures_for,
)
from billing.forms import (
    InvoiceVoidForm,
    PatientInvoiceForm,
    PaymentForm,
    pending_for_patient,
)
from billing.models import PatientInvoice, Payment, Subscription
from billing.serializers import SubscriptionSerializer
from core.managers import ProtectedRecordError
from core.mixins import ExportMixin
from core.permissions import IsClinicAdminOrReadOnly
from patients.models import Patient


class SubscriptionViewSet(ExportMixin, viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    filterset_fields = ['clinic', 'status', 'plan_name']
    ordering_fields = ['plan_name', 'status', 'starts_at', 'ends_at', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = Subscription.objects.select_related('clinic')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientInvoiceListView(AccessLogMixin, LoginRequiredMixin, ListView):
    """Listado de facturación: métricas arriba, facturas abajo, un solo filtro.

    Las dos mitades de la pantalla salen del mismo queryset (`billing.filters`),
    así que aplicar un filtro las mueve a la vez y el «total facturado» siempre
    es el de las facturas que se están viendo. La vista no decide qué entra: eso
    es `InvoiceFilters`, y aquí solo se encadena.

    Los filtros viajan por GET y la URL es la dirección real de lo que se ve:
    se puede pegar, marcar como favorita y recargar con F5. htmx es una mejora
    encima —pide la misma URL y sustituye solo la región de KPIs + tabla, con
    `hx-push-url`— y sin JavaScript el formulario y los enlaces siguen navegando
    igual. Mismo planteamiento que las pestañas de la ficha del paciente.

    Se registra un `AccessLog`: una factura cuelga de procedimientos clínicos y
    lleva el nombre del paciente, así que consultarla es una lectura de datos
    sensibles, y las lecturas no emiten señales. `AccessLogMixin` distingue solo
    la búsqueda por paciente (`q`) del listado sin filtrar.
    """

    model = PatientInvoice
    template_name = 'billing/invoice_list.html'
    #: Lo que se devuelve a htmx: KPIs + tabla + paginación, en una sola respuesta.
    partial_template_name = 'billing/_invoice_region.html'
    context_object_name = 'invoices'
    paginate_by = 20

    def is_htmx_swap(self):
        """¿Hay que devolver solo el fragmento?

        `HX-History-Restore-Request` se excluye a propósito: al volver atrás sin
        caché, htmx repite la petición para restaurar el `body` entero y
        devolverle el fragmento dejaría la página reducida a la tabla.
        """
        headers = self.request.headers
        return bool(headers.get('HX-Request')) and not headers.get('HX-History-Restore-Request')

    def get_template_names(self):
        if self.is_htmx_swap():
            return [self.partial_template_name]
        return [self.template_name]

    def get_queryset(self):
        """El queryset de la tabla; guarda por el camino el que usan los KPIs.

        Son dos formas del MISMO queryset filtrado, y el orden importa: el
        `annotate(Count(...))` se añade solo aquí, al final. Si se anotara antes
        de guardar `self.filtered_invoices`, el JOIN a los procedimientos
        multiplicaría las filas de cada factura y el `Sum('total')` de los KPIs
        saldría inflado tantas veces como líneas tenga cada una.

        De las dos anotaciones, esa es la peligrosa y sigue viviendo solo aquí.
        `with_collection()` no lo es: `amount_collected` es una subconsulta
        escalar, no un JOIN, así que no multiplica filas y convive con el
        `Count` sin falsear ninguna de las dos (ver `billing.managers`).
        """
        self.filters = InvoiceFilters.from_query(self.request.GET)
        self.filtered_invoices = self.filters.apply(invoices_for(self.request.user))

        queryset = self.filtered_invoices.select_related('patient').with_collection().annotate(
            # Una sola consulta para toda la página, en vez de un `.count()` por
            # fila. El `filter=` va DENTRO del `Count` porque una agregación sobre
            # la relación inversa no pasa por el manager de `PerformedProcedure`:
            # sin él, un procedimiento dado de baja seguiría contando.
            procedure_count=Count(
                'procedures', filter=Q(procedures__deleted_at__isnull=True),
            ),
        )
        return self.filters.order(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending = self.filters.apply_to_procedures(
            pending_procedures_for(self.request.user)
        )
        context.update({
            'section': 'billing',
            'filters': self.filters,
            'kpis': invoice_kpis(self.filtered_invoices, pending),
            'status_choices': PatientInvoice.Status.choices,
            'payment_state_choices': PatientInvoice.PaymentState.choices,
            # Base de las URLs de orden y paginación, ya normalizada. Las arma
            # el tag `{% invoice_query %}` a partir de esto, nunca de request.GET.
            'invoice_query_base': self.filters.as_params(),
            # Las cabeceras ordenables, ya resueltas: pinchar «Fecha» invierte
            # el sentido si ya se ordenaba por ella y conserva todo lo demás.
            'sort_date_query': urlencode(self.filters.toggled('date').as_params()),
            'sort_amount_query': urlencode(self.filters.toggled('amount').as_params()),
        })
        return context


# ---------------------------------------------------------------------------
# Alta y gestión de una factura
# ---------------------------------------------------------------------------

def _author_for(user):
    """El `Professional` del usuario, o `None`.

    Mismo criterio que el resto del panel (`patients.views.ProfessionalAuthorMixin`):
    un administrativo puede registrar una factura sin tener ficha de profesional,
    y por eso `created_by` admite vacío.
    """
    return getattr(user, 'professional_profile', None)


class InvoiceScopedMixin:
    """La factura de la URL, siempre acotada a la clínica del usuario.

    Una factura de otra clínica es un **404, no un 403** —y sin `AccessLog`,
    porque no se ha llegado a leer nada—, igual que en la ficha del paciente.
    Vive en un mixin porque es la comprobación que no puede faltar en ninguna de
    las cinco vistas que tocan una factura concreta.
    """

    def get_queryset(self):
        return invoices_for(self.request.user)


class ClinicRequiredMixin:
    """Sin clínica no se factura.

    Una factura toma número de la serie de SU clínica, así que un usuario de
    plataforma sin clínica asignada no tiene serie de la que tirar. Se dice y se
    devuelve al listado, en vez de reventar más abajo con un `IntegrityError`.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.clinic_id:
            messages.error(
                request,
                'Tu usuario no tiene una clínica asignada, así que no puede '
                'emitir facturas.',
            )
            return redirect('billing:invoice-list')
        return super().dispatch(request, *args, **kwargs)


class PatientInvoiceCreateView(
    AccessLogMixin, ClinicRequiredMixin, LoginRequiredMixin, FormView
):
    """Alta de una factura a mano: paciente, procedimientos y emitir o no.

    Se llega de dos sitios y el formulario es el mismo: desde «Nueva factura»
    del panel (el paciente se elige) y desde la ficha de alguien concreto
    (`?patient=<id>`, y entonces el campo queda fijo).

    El importe no se teclea en ninguno de los dos casos: sale de los
    procedimientos marcados, que ya traen su precio congelado. Lo único que se
    decide aquí es qué entra en el documento.

    Registra `AccessLog`: la lista de procedimientos pendientes es dato clínico
    (qué se le hizo al paciente y cuándo), y las lecturas no emiten señales.
    """

    template_name = 'billing/invoice_form.html'
    form_class = PatientInvoiceForm
    access_action = AccessLog.Action.VIEW

    def get_patient(self):
        """El paciente fijado por la URL, o `None` si se elige en el formulario."""
        raw = self.request.GET.get('patient') or self.request.POST.get('patient_locked')
        if not raw:
            return None
        patients = Patient.objects.all()
        if self.request.user.clinic_id:
            patients = patients.filter(clinic=self.request.user.clinic)
        return get_object_or_404(patients, pk=raw)

    def get_access_object(self):
        return self.get_patient()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['patient'] = self.get_patient()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context['form']
        context.update({
            'section': 'billing',
            'locked_patient': form.locked_patient,
            'selected_patient': form.selected_patient,
            # La lista que se pinta con casillas. Se pasa aparte del campo para
            # poder enseñar de cada procedimiento lo que ayuda a decidir —fecha,
            # zona, importe congelado— en vez de una etiqueta suelta.
            'pending_procedures': list(form.fields['procedures'].queryset),
            # Lo marcado, para que un formulario que vuelve con errores no borre
            # la selección que ya se había hecho.
            'checked_ids': {
                int(pk) for pk in self.request.POST.getlist('procedures') if pk.isdigit()
            },
        })
        return context

    @transaction.atomic
    def form_valid(self, form):
        """Crea el borrador, engancha lo marcado y, si se pidió, lo emite.

        Todo dentro de una transacción: una factura a medias —creada pero sin
        sus líneas, o emitida sin número— no es un documento, es un problema.

        Los procedimientos se enganchan UNO A UNO con `add_procedure()`, nunca
        con un `update()` masivo: ese se saltaría las señales y el cambio se
        quedaría sin rastro en el `ChangeLog`.
        """
        invoice = PatientInvoice.objects.create(
            clinic=self.request.user.clinic,
            patient=form.cleaned_data['patient'],
            created_by=_author_for(self.request.user),
        )
        try:
            for procedure in form.cleaned_data['procedures']:
                invoice.add_procedure(procedure)
            if form.cleaned_data['issue_now']:
                invoice.issue()
        except (DjangoValidationError, EmptyInvoice, InvoiceNotDraft) as exc:
            # La transacción se deshace sola al propagar; aquí solo se traduce el
            # error de dominio a algo que el formulario pueda enseñar.
            form.add_error(None, _domain_message(exc))
            transaction.set_rollback(True)
            return self.form_invalid(form)

        self.object = invoice
        if invoice.is_issued:
            messages.success(
                self.request, f'Factura {invoice.number} emitida correctamente.',
            )
        else:
            messages.success(
                self.request,
                'Borrador de factura creado. Puedes seguir añadiendo '
                'procedimientos y emitirla cuando esté completa.',
            )
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('billing:invoice-detail', args=[self.object.pk])


class InvoicePendingProceduresView(
    AccessLogMixin, ClinicRequiredMixin, LoginRequiredMixin, TemplateView
):
    """Fragmento con los procedimientos pendientes de un paciente.

    Lo pide el formulario de alta cuando se cambia el paciente del desplegable:
    la lista de lo que se puede facturar depende de a quién se factura, y esa
    lista la tiene el servidor, no el navegador. Sin JavaScript el formulario
    sigue funcionando —se elige paciente, se envía y el propio formulario vuelve
    con su lista—, así que esto solo ahorra un viaje.
    """

    template_name = 'billing/_pending_procedures.html'
    access_action = AccessLog.Action.VIEW

    def get_patient(self):
        raw = self.request.GET.get('patient')
        if not raw:
            return None
        patients = Patient.objects.all()
        if self.request.user.clinic_id:
            patients = patients.filter(clinic=self.request.user.clinic)
        return patients.filter(pk=raw).first()

    def get_access_object(self):
        return self.get_patient()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        patient = self.get_patient()
        context.update({
            'selected_patient': patient,
            'pending_procedures': list(pending_for_patient(self.request.user, patient)),
            # Al cambiar de paciente no hay nada marcado: la selección anterior
            # era de otra persona y arrastrarla sería justo lo que no se quiere.
            'checked_ids': set(),
        })
        return context


class PatientInvoiceDetailView(
    AccessLogMixin, InvoiceScopedMixin, LoginRequiredMixin, DetailView
):
    """La factura: en borrador se compone, emitida solo se lee.

    Las dos caras son la misma pantalla porque son el mismo documento en dos
    momentos, y separarlas obligaría a duplicar la cabecera y el detalle.

    Lo que se pinta cambia según el estado, y no por adorno: un borrador enseña
    sus procedimientos vivos (que se pueden quitar y añadir) y una emitida enseña
    `lines`, la copia literal de lo que se cobró. Leer los procedimientos de una
    factura emitida sería justo el error que el modelo existe para impedir:
    dar de baja un procedimiento después no puede cambiar lo que dice una factura
    ya entregada.
    """

    model = PatientInvoice
    context_object_name = 'invoice'
    template_name = 'billing/invoice_detail.html'

    def get_queryset(self):
        # `with_collection()` deja `amount_collected` —y con él `amount_due`—
        # resueltos en la MISMA consulta que trae la factura, para que el panel
        # de cobros no dispare un agregado por cada property que lee.
        return super().get_queryset().select_related('patient').with_collection()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        context['section'] = 'billing'
        context['void_form'] = InvoiceVoidForm()
        context['payment_form'] = PaymentForm()
        context['payment_methods'] = Payment.Method.choices
        if invoice.is_draft:
            context['procedures'] = list(
                invoice.procedures.order_by('performed_at', 'id')
            )
            context['pending_procedures'] = list(
                pending_for_patient(self.request.user, invoice.patient)
            )
        else:
            # Emitida o anulada: lo que dice la factura está en `lines`.
            context['lines'] = invoice.lines
            # Del cobro más antiguo al más reciente: una serie de cobros
            # parciales se lee al derecho, como un extracto, y no al revés que
            # el `ordering` del modelo (pensado para una lista suelta).
            #
            # Se pinta `frozen_created_by_name`, nunca `payment.created_by`: la
            # FK es `DO_NOTHING` + `db_constraint=False` y puede apuntar a un
            # profesional que ya no existe. Por eso tampoco hay
            # `select_related('created_by')` aquí: no haría falta y además
            # invitaría a tocar el objeto en vez de la copia congelada.
            context['payments'] = list(invoice.payments.chronological())
        return context


class InvoiceActionMixin(InvoiceScopedMixin, LoginRequiredMixin, SingleObjectMixin, View):
    """Base de las acciones sobre una factura y sus cobros. Solo POST.

    Son POST y no GET porque mueven dinero o cambian el documento: emitir gasta
    un número de la serie, anular deja una factura sin efecto para siempre y
    cobrar gasta un número de la serie de recibos y no se deshace. Un GET las
    dejaría al alcance de un prefetch del navegador o de un enlace pegado en un
    chat.

    El `except` recoge TODAS las excepciones de dominio de `billing`, no solo las
    que lanza cada acción: son estados legítimos de la aplicación —cobrar una
    factura que otro acaba de anular en otra pestaña— y tienen que salir como un
    mensaje, no como un 500.
    """

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            return self.perform(request)
        except (
            DjangoValidationError, EmptyInvoice, InvoiceNotDraft, InvoiceNotIssued,
            InvoiceHasPayments, InvoiceFrozen, InvoiceNotPayable, Overpayment,
            PaymentFrozen, ProtectedRecordError,
        ) as exc:
            messages.error(request, _domain_message(exc))
            return redirect('billing:invoice-detail', pk=self.object.pk)

    def perform(self, request):
        raise NotImplementedError


class InvoiceIssueView(InvoiceActionMixin):
    """Emite el borrador: toma número, congela importe y líneas, y lo cierra."""

    def perform(self, request):
        invoice = self.object.issue()
        messages.success(request, f'Factura {invoice.number} emitida correctamente.')
        return redirect('billing:invoice-detail', pk=invoice.pk)


class InvoiceVoidView(InvoiceActionMixin):
    """Anula una factura emitida, con su motivo.

    No corrige nada: el número se queda gastado, el documento sigue legible y sus
    procedimientos vuelven a estar pendientes de facturar.
    """

    def perform(self, request):
        form = InvoiceVoidForm(request.POST)
        if not form.is_valid():
            messages.error(request, form.errors['reason'][0])
            return redirect('billing:invoice-detail', pk=self.object.pk)

        invoice = self.object.void(reason=form.cleaned_data['reason'])
        messages.success(
            request,
            f'Factura {invoice.number} anulada. Sus procedimientos vuelven a '
            f'estar pendientes de facturar.',
        )
        return redirect('billing:invoice-detail', pk=invoice.pk)


class InvoiceProcedureView(InvoiceActionMixin):
    """Engancha o desengancha un procedimiento de un borrador.

    El procedimiento se busca SIEMPRE dentro de lo que el usuario puede tocar
    —los de la propia factura para quitar, los pendientes de su paciente para
    añadir—, así que un id manipulado no encuentra nada y sale 404. El modelo
    vuelve a comprobarlo por su cuenta en `add_procedure()`; son dos redes, y las
    dos hacen falta.
    """

    def perform(self, request):
        invoice = self.object
        action = request.POST.get('action')
        raw = request.POST.get('procedure')

        if action == 'remove':
            procedure = get_object_or_404(invoice.procedures, pk=raw)
            invoice.remove_procedure(procedure)
            messages.success(request, 'Procedimiento quitado del borrador.')
        elif action == 'add':
            procedure = get_object_or_404(
                pending_for_patient(request.user, invoice.patient), pk=raw,
            )
            invoice.add_procedure(procedure)
            messages.success(request, 'Procedimiento añadido al borrador.')
        else:
            messages.error(request, 'Acción no reconocida.')

        return redirect('billing:invoice-detail', pk=invoice.pk)


class InvoicePaymentCreateView(InvoiceActionMixin):
    """Registra un cobro contra una factura emitida.

    La vista no valida nada del dominio: construye el `Payment` y deja hablar al
    `save()` del modelo. Ahí está el `select_for_update()` sobre la factura, que
    es lo único que impide el sobrepago concurrente, y ahí se toma el número de
    recibo. `InvoiceActionMixin` traduce lo que salga —`InvoiceNotPayable` si
    alguien cobra un borrador con la URL a mano, `Overpayment` si se pasa del
    pendiente— a un mensaje y devuelve al detalle.

    No lleva `ClinicRequiredMixin`, al revés que el alta de facturas: la clínica
    del cobro la HEREDA de la factura, y la factura ya viene acotada por
    `InvoiceScopedMixin`. La serie de recibos que se gasta es la de esa factura,
    no la del usuario.
    """

    def perform(self, request):
        invoice = self.object
        form = PaymentForm(request.POST)
        if not form.is_valid():
            messages.error(request, _first_error(form))
            return redirect('billing:invoice-detail', pk=invoice.pk)

        # `paid_at` solo se pasa si se tecleó: si no, manda el `default=now` del
        # modelo. Pasarlo como `None` rompería el `NOT NULL` de la columna.
        extra = {}
        if form.cleaned_data.get('paid_at'):
            extra['paid_at'] = form.cleaned_data['paid_at']

        payment = Payment.objects.create(
            invoice=invoice,
            amount=form.cleaned_data['amount'],
            method=form.cleaned_data['method'],
            created_by=_author_for(request.user),
            **extra,
        )
        messages.success(
            request,
            f'Cobro {payment.receipt_number} registrado: {payment.amount} € '
            f'por {payment.get_method_display().lower()}.',
        )
        return redirect('billing:invoice-detail', pk=invoice.pk)


class InvoiceDeleteView(InvoiceActionMixin):
    """Tira un borrador. Una factura emitida no se borra: se anula.

    El veto no está aquí sino en el modelo (`can_be_deleted()`), que lanza
    `ProtectedRecordError`; esta vista solo lo traduce a un mensaje.
    """

    def perform(self, request):
        invoice = self.object
        invoice.delete()
        messages.success(
            request,
            'Borrador eliminado. Sus procedimientos vuelven a estar pendientes '
            'de facturar.',
        )
        return redirect('billing:invoice-list')


def _first_error(form) -> str:
    """El primer error del formulario, en el orden en que se declararon.

    Las acciones sobre una factura redirigen al detalle y hablan por `messages`
    (igual que `InvoiceVoidView`), así que el formulario no se vuelve a pintar y
    hay que elegir UN mensaje. Se recorre `form.fields` y no `form.errors` para
    que ante la misma entrada el elegido sea siempre el mismo.
    """
    for name in form.fields:
        if name in form.errors:
            return form.errors[name][0]
    non_field = form.non_field_errors()
    return non_field[0] if non_field else 'Revisa los datos del cobro.'


def _domain_message(exc) -> str:
    """Texto legible de un error de dominio o de validación.

    Las excepciones de `billing.exceptions` ya traen un mensaje escrito para
    leerse; un `ValidationError` de Django trae una lista y hay que aplanarla.
    """
    if isinstance(exc, DjangoValidationError):
        return ' '.join(exc.messages)
    return str(exc)
