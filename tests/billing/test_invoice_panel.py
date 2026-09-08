"""Panel de facturación: el filtro compartido, los KPIs y el listado.

Lo que se vigila aquí no es que la pantalla pinte, sino las tres trampas que
tiene debajo: que los KPIs y la tabla salgan del mismo queryset, que el JOIN a
los procedimientos no infle el `Sum('total')`, y que el número de procedimientos
no dispare una consulta por fila.
"""
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from billing.filters import InvoiceFilters, invoice_kpis, invoices_for, pending_procedures_for
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
