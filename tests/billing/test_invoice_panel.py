"""Panel de facturación: el filtro compartido, los KPIs y el listado.

Lo que se vigila aquí no es que la pantalla pinte, sino las tres trampas que
tiene debajo: que los KPIs y la tabla salgan del mismo queryset, que el JOIN a
los procedimientos no infle el `Sum('total')`, y que el número de procedimientos
no dispare una consulta por fila.
"""
from decimal import Decimal

import pytest
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from billing.filters import (
    COLLECTION_PENDING,
    InvoiceFilters,
    invoice_kpis,
    invoices_for,
    pending_procedures_for,
)
from billing.models import PatientInvoice
from clinical.models import PerformedProcedure

LIST_URL = reverse('billing:invoice-list')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


def _kpis(user, params=None):
    """Los KPIs tal y como los calcula la vista, sin pasar por HTTP."""
    filters = InvoiceFilters.from_query(params or {})
    invoices = filters.apply(invoices_for(user))
    pending = filters.apply_to_procedures(pending_procedures_for(user))
    return invoices, invoice_kpis(invoices, pending)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_kpis_solo_cuentan_lo_emitido(admin_user, issued_invoice_a):
    """Lo facturado mira las emitidas; el recuento, todas las que se ven."""
    PatientInvoice.objects.create(clinic=admin_user.clinic, patient=issued_invoice_a.patient)

    _, kpis = _kpis(admin_user)

    assert kpis['invoice_count'] == 2          # borrador incluido
    assert kpis['issued_count'] == 1
    assert kpis['total_billed'] == Decimal('50.00')
    assert kpis['average_ticket'] == Decimal('50.00')


@pytest.mark.django_db
def test_el_total_no_se_infla_con_varias_lineas(admin_user, draft_invoice_a, visit_a, service_a):
    """Regresión: el JOIN a `procedures` multiplicaba las filas de la factura.

    Con tres procedimientos dentro, un `Sum('total')` sobre un queryset anotado
    con `Count('procedures')` devolvería el triple. Por eso el `annotate` se
    añade solo al queryset de la tabla, nunca al que agregan los KPIs.
    """
    for _ in range(3):
        draft_invoice_a.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
    invoice = draft_invoice_a.issue()

    _, kpis = _kpis(admin_user)

    assert invoice.total == Decimal('150.00')
    assert kpis['total_billed'] == Decimal('150.00')
    assert kpis['invoice_count'] == 1


@pytest.mark.django_db
def test_kpis_y_listado_se_mueven_juntos(admin_user, issued_invoice_a):
    """Filtrar por «borrador» deja la tabla en la factura borrador y el total a 0."""
    draft = PatientInvoice.objects.create(
        clinic=admin_user.clinic, patient=issued_invoice_a.patient,
    )

    invoices, kpis = _kpis(admin_user, {'status': PatientInvoice.Status.DRAFT})

    assert list(invoices) == [draft]
    assert kpis['invoice_count'] == 1
    assert kpis['total_billed'] == Decimal('0.00')


@pytest.mark.django_db
def test_pendiente_de_facturar_ignora_lo_ya_facturado(admin_user, procedure_a, visit_a, service_a):
    """Es otro queryset: procedimientos sin factura, no facturas en algún estado."""
    otro = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

    _, antes = _kpis(admin_user)
    assert antes['pending_count'] == 2
    assert antes['pending_amount'] == Decimal('100.00')

    invoice = PatientInvoice.objects.create(clinic=admin_user.clinic, patient=procedure_a.visit.episode.history.patient)
    invoice.add_procedure(otro)

    _, despues = _kpis(admin_user)
    assert despues['pending_count'] == 1
    assert despues['pending_amount'] == Decimal('50.00')


@pytest.mark.django_db
def test_pendiente_de_facturar_respeta_el_borrado_logico(admin_user, procedure_a):
    procedure_a.delete()

    _, kpis = _kpis(admin_user)

    assert kpis['pending_count'] == 0
    assert kpis['pending_amount'] == Decimal('0.00')


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_filtro_por_importe_y_por_paciente(admin_user, issued_invoice_a):
    patient = issued_invoice_a.patient

    assert list(_kpis(admin_user, {'min': '10', 'max': '100'})[0]) == [issued_invoice_a]
    assert list(_kpis(admin_user, {'min': '60'})[0]) == []
    assert list(_kpis(admin_user, {'q': patient.last_name})[0]) == [issued_invoice_a]
    assert list(_kpis(admin_user, {'q': 'inexistente'})[0]) == []


@pytest.mark.django_db
def test_filtro_por_rango_de_fechas(admin_user, issued_invoice_a):
    hoy = timezone.localdate().isoformat()

    assert list(_kpis(admin_user, {'desde': hoy, 'hasta': hoy})[0]) == [issued_invoice_a]
    assert list(_kpis(admin_user, {'desde': '2000-01-01', 'hasta': '2000-12-31'})[0]) == []


@pytest.mark.django_db
def test_parametros_invalidos_no_rompen_el_listado(admin_user, issued_invoice_a):
    """Una URL pegada a mano cae a los valores por defecto, no a un 500."""
    filters = InvoiceFilters.from_query(
        {'status': 'inventado', 'sort': 'rm -rf', 'direction': 'x', 'min': 'diez', 'desde': 'ayer'}
    )

    assert filters.status == ''
    assert filters.sort == 'date'
    assert filters.direction == 'desc'
    assert filters.amount_min is None
    assert filters.date_from is None


@pytest.mark.django_db
def test_un_rango_del_reves_se_endereza(admin_user):
    filters = InvoiceFilters.from_query({'desde': '2026-03-01', 'hasta': '2026-01-01', 'min': '90', 'max': '10'})

    assert filters.date_from.isoformat() == '2026-01-01'
    assert filters.amount_min == Decimal('10')


# ---------------------------------------------------------------------------
# Multi-tenancy
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cada_clinica_ve_solo_sus_facturas(admin_user_b, issued_invoice_a):
    invoices, kpis = _kpis(admin_user_b)

    assert list(invoices) == []
    assert kpis['total_billed'] == Decimal('0.00')


# ---------------------------------------------------------------------------
# La vista
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_pagina_completa_trae_la_region(panel_client, issued_invoice_a):
    response = panel_client.get(LIST_URL)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="billing-region"' in body
    assert 'id="invoice-filters"' in body
    assert issued_invoice_a.number in body


@pytest.mark.django_db
def test_htmx_devuelve_solo_el_fragmento(panel_client, issued_invoice_a):
    response = panel_client.get(LIST_URL, HTTP_HX_REQUEST='true')

    body = response.content.decode()
    assert response.status_code == 200
    assert 'id="billing-region"' not in body   # el contenedor no viaja, solo su contenido
    assert '<form id="invoice-filters"' not in body
    assert 'Total facturado' in body           # KPIs...
    assert issued_invoice_a.number in body     # ...y tabla, en la misma respuesta


@pytest.mark.django_db
def test_restaurar_el_historial_devuelve_la_pagina_entera(panel_client, issued_invoice_a):
    """htmx repite la petición para restaurar el `body`: darle el fragmento lo rompería."""
    response = panel_client.get(
        LIST_URL, HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true',
    )

    assert 'id="billing-region"' in response.content.decode()


@pytest.mark.django_db
def test_el_numero_de_procedimientos_no_hace_una_consulta_por_fila(
    django_assert_max_num_queries, panel_client, clinic_a, patient_a, visit_a, service_a,
):
    for _ in range(5):
        invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        invoice.add_procedure(PerformedProcedure.objects.create(visit=visit_a, service=service_a))
        invoice.issue()

    # Sesión, usuario, KPIs (2), listado, recuento del paginador, AccessLog…
    # El margen es amplio a propósito: lo que se vigila es que NO crezca con las
    # filas, y con `.count()` por fila esto se iría a más de veinte.
    with django_assert_max_num_queries(15):
        response = panel_client.get(LIST_URL)

    assert response.status_code == 200


@pytest.mark.django_db
def test_la_lectura_deja_rastro_en_el_audit(panel_client, issued_invoice_a):
    from audit.models import AccessLog

    panel_client.get(LIST_URL, {'q': 'perez'})

    log = AccessLog.objects.latest('timestamp')
    assert log.action == AccessLog.Action.SEARCH
    assert log.path == LIST_URL


# ---------------------------------------------------------------------------
# Estado de cobro: la anotación que convive con el recuento de procedimientos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_el_estado_de_cobro_y_el_recuento_de_procedimientos_conviven(
    admin_user, clinic_a, patient_a, visit_a, service_a,
):
    """Las dos anotaciones del listado no se pisan.

    Es el test que sostiene que `amount_collected` sea una subconsulta escalar y
    no un `Sum` con JOIN: con el JOIN, las tres filas de procedimientos
    multiplicaban la suma de los pagos y `amount_collected` salía a 60,00 € en
    vez de a 20,00 €. Al revés también: el `Count` se inflaba con los cobros.
    """
    from billing.models import Payment

    invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
    for _ in range(3):
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
    invoice.issue()
    Payment.objects.create(
        invoice=invoice, amount=Decimal('20.00'), method=Payment.Method.CARD,
    )

    row = (
        invoices_for(admin_user)
        .with_collection()
        .annotate(
            procedure_count=Count(
                'procedures', filter=Q(procedures__deleted_at__isnull=True),
            ),
        )
        .get(pk=invoice.pk)
    )

    assert row.procedure_count == 3
    assert row.amount_collected == Decimal('20.00')
    assert row.total == Decimal('150.00')
    assert row.payment_state == PatientInvoice.PaymentState.PARTIAL


@pytest.mark.django_db
def test_el_listado_ensena_el_estado_de_cobro(panel_client, issued_invoice_a):
    from billing.models import Payment

    Payment.objects.create(
        invoice=issued_invoice_a, amount=Decimal('20.00'), method=Payment.Method.CARD,
    )

    body = panel_client.get(LIST_URL).content.decode()

    assert 'Parcial' in body


@pytest.mark.django_db
def test_el_borrador_no_ensena_estado_de_cobro(panel_client, draft_invoice_a):
    """Un borrador sale «impagado» de la anotación, y decirlo sería mentir.

    Su importe todavía puede cambiar: no debe nada que se pueda dejar de pagar.
    Se mira el CHIP de la fila (el enlace que filtra), no el texto suelto: la
    palabra «Impagada» aparece también en el desplegable del filtro, que está
    siempre.
    """
    body = panel_client.get(LIST_URL).content.decode()

    assert 'cobro=unpaid' not in body


# ---------------------------------------------------------------------------
# Filtro por estado de cobro
# ---------------------------------------------------------------------------

@pytest.fixture
def three_collection_states(db, clinic_a, patient_a, visit_a, service_a):
    """Tres facturas emitidas de 50 €: sin cobrar, a medias y saldada."""
    from billing.models import Payment

    invoices = []
    for _ in range(3):
        invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
        invoices.append(invoice.issue())

    unpaid, partial, paid = invoices
    Payment.objects.create(
        invoice=partial, amount=Decimal('20.00'), method=Payment.Method.CARD,
    )
    Payment.objects.create(
        invoice=paid, amount=Decimal('50.00'), method=Payment.Method.CASH,
    )
    return {'unpaid': unpaid, 'partial': partial, 'paid': paid}


@pytest.mark.django_db
def test_filtro_por_estado_de_cobro(admin_user, three_collection_states):
    invoices, _ = _kpis(admin_user, {'cobro': 'partial'})

    assert list(invoices) == [three_collection_states['partial']]


@pytest.mark.django_db
def test_el_filtro_de_cobro_no_arrastra_borradores(
    admin_user, three_collection_states, draft_invoice_a,
):
    """Filtrar «impagadas» no puede devolver algo que aún no debe nada."""
    invoices, _ = _kpis(admin_user, {'cobro': 'unpaid'})

    assert list(invoices) == [three_collection_states['unpaid']]


@pytest.mark.django_db
def test_con_saldo_agrupa_impagadas_y_parciales(admin_user, three_collection_states):
    """«¿A quién hay que reclamar?» no es una pregunta sobre un solo estado.

    Una factura cobrada a medias se reclama igual que una que no se ha tocado, y
    sin este valor había que mirar dos listas para saberlo. Es además lo que hace
    que el «por cobrar» del panel de control lleve exactamente a estas facturas.
    """
    invoices, _ = _kpis(admin_user, {'cobro': COLLECTION_PENDING})

    assert set(invoices) == {
        three_collection_states['unpaid'], three_collection_states['partial'],
    }
    assert three_collection_states['paid'] not in invoices


@pytest.mark.django_db
def test_con_saldo_tampoco_arrastra_borradores_ni_anuladas(
    admin_user, three_collection_states, draft_invoice_a, clinic_a, patient_a,
    visit_a, service_a,
):
    """Un borrador aún puede cambiar de importe y una anulada dejó de deber."""
    voided = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
    voided.add_procedure(
        PerformedProcedure.objects.create(visit=visit_a, service=service_a)
    )
    voided.issue().void(reason='Error en el importe')

    invoices, _ = _kpis(admin_user, {'cobro': COLLECTION_PENDING})

    assert draft_invoice_a not in invoices
    assert voided not in invoices


@pytest.mark.django_db
def test_con_saldo_cuadra_con_el_kpi_de_impagadas(admin_user, three_collection_states):
    """La lista y el número que lleva a ella tienen que decir lo mismo."""
    invoices, _ = _kpis(admin_user, {'cobro': COLLECTION_PENDING})
    _, kpis = _kpis(admin_user)

    assert invoices.count() == kpis['unpaid_invoice_count'] == 2


@pytest.mark.django_db
def test_un_estado_de_cobro_inventado_se_ignora():
    filters = InvoiceFilters.from_query({'cobro': 'rm -rf'})

    assert filters.collection == ''


@pytest.mark.django_db
def test_el_filtro_de_cobro_sobrevive_al_orden_y_a_la_paginacion():
    filters = InvoiceFilters.from_query({'cobro': 'paid', 'q': 'ana'})

    params = filters.toggled('amount').as_params()

    assert params['cobro'] == 'paid'
    assert params['q'] == 'ana'


# ---------------------------------------------------------------------------
# KPIs de cobro
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_kpi_pendiente_de_cobro(admin_user, issued_invoice_a):
    from billing.models import Payment

    Payment.objects.create(
        invoice=issued_invoice_a, amount=Decimal('20.00'), method=Payment.Method.CARD,
    )

    _, kpis = _kpis(admin_user)

    assert kpis['pending_collection_amount'] == Decimal('30.00')
    assert kpis['unpaid_invoice_count'] == 1


@pytest.mark.django_db
def test_el_kpi_de_cobro_ignora_borradores_y_anuladas(
    admin_user, clinic_a, patient_a, visit_a, service_a, draft_invoice_a,
):
    """Solo se reclama lo emitido y vigente."""
    voided = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
    voided.add_procedure(
        PerformedProcedure.objects.create(visit=visit_a, service=service_a)
    )
    voided.issue()
    voided.void(reason='Error')

    _, kpis = _kpis(admin_user)

    assert kpis['pending_collection_amount'] == Decimal('0.00')
    assert kpis['unpaid_invoice_count'] == 0


@pytest.mark.django_db
def test_el_pendiente_de_cobro_no_se_infla_con_varias_lineas(
    admin_user, clinic_a, patient_a, visit_a, service_a,
):
    """Gemelo de `test_el_total_no_se_infla_con_varias_lineas`, para el cobro.

    Tres procedimientos y un cobro: si `amount_collected` volviera a ser un
    `Sum` con JOIN, la resta se haría tres veces y el pendiente saldría a 300 €.
    """
    from billing.models import Payment

    invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
    for _ in range(3):
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
    invoice.issue()
    Payment.objects.create(
        invoice=invoice, amount=Decimal('50.00'), method=Payment.Method.CARD,
    )

    _, kpis = _kpis(admin_user)

    assert kpis['total_billed'] == Decimal('150.00')
    assert kpis['pending_collection_amount'] == Decimal('100.00')


@pytest.mark.django_db
def test_el_listado_no_crece_en_consultas_con_los_cobros(
    django_assert_max_num_queries, panel_client, clinic_a, patient_a, visit_a, service_a,
):
    """El mismo presupuesto que sin cobros: el estado va anotado, no leído."""
    from billing.models import Payment

    for _ in range(5):
        invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
        invoice.issue()
        Payment.objects.create(
            invoice=invoice, amount=Decimal('25.00'), method=Payment.Method.CARD,
        )

    with django_assert_max_num_queries(15):
        response = panel_client.get(LIST_URL)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Los filtros plegables
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_los_campos_plegables_siguen_dentro_del_formulario(panel_client):
    """Plegar es cosa del navegador; enviar los filtros, del formulario.

    El bloque que se pliega tiene que quedar DENTRO de `<form
    id="invoice-filters">`: si un refactor lo sacara, los campos dejarían de
    enviarse y el listado ignoraría los filtros sin que fallara nada — el peor
    tipo de rotura, la silenciosa. Aquí se comprueba la anidación real.
    """
    body = panel_client.get(LIST_URL).content.decode()

    start = body.index('id="invoice-filters"')
    form = body[start:body.index('</form>', start)]

    assert 'id="invoice-filter-fields"' in form
    for name in ('q', 'status', 'cobro', 'desde', 'hasta', 'min', 'max'):
        assert f'name="{name}"' in form


@pytest.mark.django_db
def test_el_spinner_no_se_pliega_con_los_filtros(panel_client):
    """Lo comparten las cabeceras ordenables y la paginación.

    Dentro del bloque plegable dejaría de avisar de la carga justo cuando los
    filtros están escondidos, que es cuando más se usa la tabla.
    """
    body = panel_client.get(LIST_URL).content.decode()

    start = body.index('id="invoice-filters"')
    form = body[start:body.index('</form>', start)]

    assert form.index('id="billing-spinner"') < form.index('id="invoice-filter-fields"')


@pytest.mark.django_db
def test_el_boton_de_restablecer_lo_gobierna_el_cliente(panel_client):
    """El servidor pinta el estado inicial; a partir de ahí manda Alpine.

    El formulario vive FUERA de `#billing-region`, que es lo único que htmx
    sustituye, así que un `{% if filters.has_filters %}` se quedaba congelado en
    lo que fuera cierto al cargar: el botón desaparecía al pulsarlo —correcto,
    ya no hay nada que restablecer— y no volvía al filtrar de nuevo. De ahí el
    `x-show`, con el `style` inicial para el instante previo a Alpine y para
    quien navegue sin JavaScript.
    """
    sin_filtros = panel_client.get(LIST_URL).content.decode()
    con_filtros = panel_client.get(LIST_URL, {'q': 'john'}).content.decode()

    def bloque(body):
        start = body.index('id="invoice-filters"')
        form = body[start:body.index('</form>', start)]
        return form[form.index('Restablecer') - 400:form.index('Restablecer')]

    assert 'x-show="activos.length"' in bloque(sin_filtros)
    # Sin filtros no se ve, ni siquiera antes de que cargue Alpine.
    assert 'display: none' in bloque(sin_filtros)
    # Con filtros sí, y sin depender de JavaScript para aparecer.
    assert 'display: none' not in bloque(con_filtros)
