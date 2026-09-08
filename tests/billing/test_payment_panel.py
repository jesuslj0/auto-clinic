"""Registro de cobros desde el panel, y el panel que los enseña.

Lo que se vigila es que la interfaz no pueda hacer lo que el modelo prohíbe
—cobrar un borrador, cobrar de más, cobrar la factura de otra clínica— y que
esos intentos salgan como un mensaje y no como un 500. Las reglas viven en
`Payment.save()`; aquí se comprueba que la vista las deja hablar y las traduce.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import PatientInvoice, Payment, ReceiptSequence
from clinical.models import PerformedProcedure


@pytest.fixture
def panel(client, admin_user):
    client.force_login(admin_user)
    return client


def pay_url(invoice):
    return reverse('billing:invoice-payment', args=[invoice.pk])


def detail_url(invoice):
    return reverse('billing:invoice-detail', args=[invoice.pk])


def messages_of(response):
    """Los mensajes encolados, en texto plano."""
    return [str(m) for m in response.wsgi_request._messages]


# ---------------------------------------------------------------------------
# Alta de un cobro
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_registrar_un_cobro_desde_el_panel(panel, issued_invoice_a, clinic_a):
    response = panel.post(
        pay_url(issued_invoice_a), {'amount': '20.00', 'method': 'card'},
    )

    assert response.status_code == 302
    assert response.url == detail_url(issued_invoice_a)

    payment = Payment.objects.get()
    assert payment.invoice == issued_invoice_a
    assert payment.amount == Decimal('20.00')
    assert payment.method == Payment.Method.CARD
    assert payment.clinic == clinic_a
    # El recibo nace con su número y con la copia de la factura que cobra.
    assert payment.receipt_number.startswith('R-')
    assert payment.frozen_invoice_number == issued_invoice_a.number


@pytest.mark.django_db
def test_el_cobro_se_atribuye_a_quien_lo_registra(panel, issued_invoice_a, admin_user):
    panel.post(pay_url(issued_invoice_a), {'amount': '20.00', 'method': 'cash'})

    payment = Payment.objects.get()
    assert payment.created_by == admin_user.professional_profile
    assert payment.frozen_created_by_name == str(admin_user.professional_profile)


@pytest.mark.django_db
def test_el_importe_admite_coma_decimal(panel, issued_invoice_a):
    """El panel está en español y quien cobra teclea «20,50», no «20.50»."""
    panel.post(pay_url(issued_invoice_a), {'amount': '20,50', 'method': 'bizum'})

    assert Payment.objects.get().amount == Decimal('20.50')


@pytest.mark.django_db
def test_la_fecha_vacia_toma_el_momento_del_cobro(panel, issued_invoice_a):
    from django.utils import timezone

    panel.post(pay_url(issued_invoice_a), {'amount': '20.00', 'method': 'card'})

    payment = Payment.objects.get()
    assert (timezone.now() - payment.paid_at).total_seconds() < 60


@pytest.mark.django_db
def test_una_fecha_tecleada_se_respeta(panel, issued_invoice_a):
    """El dinero pudo entrar ayer y registrarse hoy."""
    panel.post(pay_url(issued_invoice_a), {
        'amount': '20.00', 'method': 'transfer', 'paid_at': '2026-01-15T10:30',
    })

    payment = Payment.objects.get()
    assert (payment.paid_at.year, payment.paid_at.month, payment.paid_at.day) == (2026, 1, 15)
    assert payment.paid_at.tzinfo is not None


@pytest.mark.django_db
def test_un_importe_de_cero_se_rechaza(panel, issued_invoice_a):
    response = panel.post(pay_url(issued_invoice_a), {'amount': '0', 'method': 'card'})

    assert response.status_code == 302
    assert Payment.objects.count() == 0
    assert 'cero euros' in ' '.join(messages_of(response))


@pytest.mark.django_db
def test_un_metodo_inventado_se_rechaza(panel, issued_invoice_a):
    response = panel.post(
        pay_url(issued_invoice_a), {'amount': '20.00', 'method': 'crypto'},
    )

    assert response.status_code == 302
    assert Payment.objects.count() == 0


# ---------------------------------------------------------------------------
# Las reglas del modelo, vistas desde la interfaz
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_el_sobrepago_se_rechaza_desde_la_vista(panel, issued_invoice_a):
    """Una factura de 50 € no admite 30 € + 30 €."""
    panel.post(pay_url(issued_invoice_a), {'amount': '30.00', 'method': 'card'})

    response = panel.post(pay_url(issued_invoice_a), {'amount': '30.00', 'method': 'card'})

    assert response.status_code == 302
    assert Payment.objects.count() == 1
    assert 'no puede superar lo que queda pendiente' in ' '.join(messages_of(response))


@pytest.mark.django_db
def test_no_se_cobra_un_borrador(panel, draft_invoice_a):
    """Contra un borrador no entra dinero, y decirlo no es un 500.

    Es el test que vigila que `InvoiceNotPayable` esté en el `except` de
    `InvoiceActionMixin`: la interfaz no ofrece el botón, pero la URL se puede
    escribir a mano y el modelo lanza una excepción de dominio, no un
    `ValidationError`.
    """
    response = panel.post(pay_url(draft_invoice_a), {'amount': '10.00', 'method': 'cash'})

    assert response.status_code == 302
    assert Payment.objects.count() == 0
    assert 'Solo se cobra una factura emitida' in ' '.join(messages_of(response))


@pytest.mark.django_db
def test_no_se_cobra_una_anulada(panel, issued_invoice_a):
    issued_invoice_a.void(reason='Error en los procedimientos')

    response = panel.post(pay_url(issued_invoice_a), {'amount': '10.00', 'method': 'cash'})

    assert response.status_code == 302
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_la_serie_de_recibos_no_se_gasta_en_un_intento_rechazado(panel, issued_invoice_a):
    """Un cobro que no llega a existir no puede dejar un hueco en la serie.

    Es el test que detectaría un `ModelForm` (o cualquier `full_clean()` colado
    en el camino): `Payment.clean()` toma número de la serie, así que validar
    sin guardar quemaría un `R-…` que nunca llegaría a ser un recibo.
    """
    panel.post(pay_url(issued_invoice_a), {'amount': '999.00', 'method': 'card'})

    assert Payment.objects.count() == 0
    assert not ReceiptSequence.objects.exists()


@pytest.mark.django_db
def test_el_cobro_no_admite_get(panel, issued_invoice_a):
    """Cobrar mueve dinero: un GET lo dejaría al alcance de un prefetch."""
    assert panel.get(pay_url(issued_invoice_a)).status_code == 405


@pytest.mark.django_db
def test_cobrar_la_factura_de_otra_clinica_es_un_404(client, admin_user_b, issued_invoice_a):
    client.force_login(admin_user_b)

    response = client.post(pay_url(issued_invoice_a), {'amount': '10.00', 'method': 'card'})

    assert response.status_code == 404
    assert Payment.objects.count() == 0


# ---------------------------------------------------------------------------
# El panel del detalle
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_el_detalle_ensena_el_panel_de_cobros(panel, issued_invoice_a):
    payment = Payment.objects.create(
        invoice=issued_invoice_a, amount=Decimal('20.00'), method=Payment.Method.CARD,
    )

    body = panel.get(detail_url(issued_invoice_a)).content.decode()

    assert payment.receipt_number in body
    assert 'Tarjeta' in body
    assert 'Parcial' in body
    # Cobrado 20 de 50: quedan 30.
    assert '30,00' in body


@pytest.mark.django_db
def test_el_borrador_no_ofrece_registrar_cobro(panel, draft_invoice_a):
    body = panel.get(detail_url(draft_invoice_a)).content.decode()

    assert pay_url(draft_invoice_a) not in body
    assert 'Un borrador todavía no se cobra' in body


@pytest.mark.django_db
def test_una_factura_saldada_no_ofrece_registrar_cobro(panel, issued_invoice_a):
    Payment.objects.create(
        invoice=issued_invoice_a, amount=Decimal('50.00'), method=Payment.Method.CASH,
    )

    body = panel.get(detail_url(issued_invoice_a)).content.decode()

    assert 'Pagada' in body
    assert pay_url(issued_invoice_a) not in body


@pytest.mark.django_db
def test_el_panel_no_hace_una_consulta_por_cobro(
    django_assert_max_num_queries, panel, issued_invoice_a,
):
    for _ in range(5):
        Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('10.00'), method=Payment.Method.CARD,
        )

    # Sesión, usuario, factura (con su subconsulta), cobros, AccessLog… El
    # margen es amplio a propósito: lo que se vigila es que NO crezca con los
    # recibos, y leyendo `payment.created_by` por fila esto se dispararía.
    with django_assert_max_num_queries(12):
        response = panel.get(detail_url(issued_invoice_a))

    assert response.status_code == 200
