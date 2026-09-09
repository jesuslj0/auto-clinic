"""Cobrar desde el listado, sin entrar en cada factura.

Un cobro no existe sin factura —`Payment` hereda de ella la clínica, valida el
sobrepago contra su pendiente y gasta un número de su serie de recibos—, así que
no hay «formulario de pagos» suelto: se cobra desde la fila de la factura, que es
donde el dato ya está. El listado filtrado por `cobro=pending` es la cola de
trabajo, y el botón de cada fila abre el mismo formulario de siempre.

Lo que se defiende aquí: el botón solo aparece donde el cobro es posible, el
fragmento no se puede abrir a mano para una factura que no lo admite, se vuelve
al listado tal y como estaba, y `next` no es una puerta a redirigir a cualquier
sitio.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import PatientInvoice, Payment
from clinical.models import PerformedProcedure

LIST_URL = reverse('billing:invoice-list')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


def _form_url(invoice, **params):
    url = reverse('billing:invoice-payment-form', args=[invoice.pk])
    if params:
        from urllib.parse import urlencode
        url = f'{url}?{urlencode(params)}'
    return url


# ---------------------------------------------------------------------------
# El botón de la fila
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestElBotonDeLaFila:
    def test_una_emitida_con_saldo_ofrece_cobrar(self, panel_client, issued_invoice_a):
        body = panel_client.get(LIST_URL).content.decode()

        assert _form_url(issued_invoice_a) in body
        assert 'Cobrar' in body

    def test_una_saldada_ya_no_lo_ofrece(self, panel_client, issued_invoice_a):
        """No hay nada que cobrar: el modelo lo rechazaría con un `Overpayment`."""
        Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('50.00'),
            method=Payment.Method.CARD,
        )

        body = panel_client.get(LIST_URL).content.decode()

        assert _form_url(issued_invoice_a) not in body

    def test_una_cobrada_a_medias_sigue_ofreciendolo(self, panel_client, payment_a, issued_invoice_a):
        body = panel_client.get(LIST_URL).content.decode()

        assert _form_url(issued_invoice_a) in body

    def test_un_borrador_no_lo_ofrece(self, panel_client, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        body = panel_client.get(LIST_URL).content.decode()

        assert _form_url(draft_invoice_a) not in body

    def test_una_anulada_no_lo_ofrece(self, panel_client, issued_invoice_a):
        issued_invoice_a.void(reason='Error en el importe')

        body = panel_client.get(LIST_URL).content.decode()

        assert _form_url(issued_invoice_a) not in body

    def test_cobrar_no_estira_la_fila(self, panel_client, issued_invoice_a):
        """Es texto, no un botón con borde.

        Un `.btn-outlined` mide unos diez píxeles más que el `text-sm` del resto
        de la fila, así que las filas cobrables quedaban más altas que las demás
        y la tabla perdía su pauta. En la tarjeta de móvil sí sigue siendo un
        botón: allí las alturas ya son distintas y el dedo necesita superficie.
        """
        body = panel_client.get(LIST_URL).content.decode()
        # Desde el `<table>`: la primera aparición del enlace está en la tarjeta
        # de móvil, que se pinta antes y sí lleva botón.
        tabla = body[body.index('<table'):]
        i = tabla.index('cobrar/formulario/')
        celda = tabla[tabla.rindex('<td', 0, i):tabla.index('</td>', i)]

        assert 'btn-outlined' not in celda
        assert 'text-sm' in celda

    def test_las_filas_llevan_a_su_factura(self, panel_client, issued_invoice_a):
        """La fila entera es clicable, no solo el número."""
        body = panel_client.get(LIST_URL).content.decode()
        detail = reverse('billing:invoice-detail', args=[issued_invoice_a.pk])

        assert f'data-row-href="{detail}"' in body


# ---------------------------------------------------------------------------
# El fragmento del modal
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestElFragmentoDelModal:
    def test_trae_el_formulario_de_esa_factura(self, panel_client, issued_invoice_a):
        response = panel_client.get(_form_url(issued_invoice_a))
        body = response.content.decode()

        assert response.status_code == 200
        assert reverse('billing:invoice-payment', args=[issued_invoice_a.pk]) in body
        assert 'Registrar un cobro' in body

    def test_prellena_lo_que_queda_pendiente(self, panel_client, payment_a, issued_invoice_a):
        """Cobrar todo lo que se debe es el caso normal: un solo clic.

        Con la coma decimal, que es como se teclea aquí y lo que `PaymentForm`
        acepta (`localize=True`): el valor prellenado tiene que poder reenviarse
        tal cual sin tocarlo.
        """
        body = panel_client.get(_form_url(issued_invoice_a)).content.decode()

        assert 'value="30,00"' in body

    def test_una_factura_saldada_no_abre_formulario(self, panel_client, issued_invoice_a):
        """Aunque se llegue con la URL a mano."""
        Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('50.00'),
            method=Payment.Method.CARD,
        )

        assert panel_client.get(_form_url(issued_invoice_a)).status_code == 404

    def test_un_borrador_no_abre_formulario(self, panel_client, draft_invoice_a):
        assert panel_client.get(_form_url(draft_invoice_a)).status_code == 404

    def test_no_se_asoma_a_otra_clinica(self, client, admin_user_b, issued_invoice_a):
        client.force_login(admin_user_b)

        assert client.get(_form_url(issued_invoice_a)).status_code == 404

    def test_no_registra_nada(self, panel_client, issued_invoice_a):
        """Es un GET: pinta el formulario y no toca el dinero ni la serie."""
        panel_client.get(_form_url(issued_invoice_a))

        assert not Payment.objects.exists()


# ---------------------------------------------------------------------------
# La vuelta al listado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLaVueltaAlListado:
    def test_cobrar_desde_el_listado_devuelve_al_listado(self, panel_client, issued_invoice_a):
        """Con sus filtros: quien recorría los impagados no pierde el sitio."""
        back = f'{LIST_URL}?cobro=pending&sort=amount'

        response = panel_client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '20,00', 'method': Payment.Method.CARD, 'next': back},
        )

        assert response.status_code == 302
        assert response.url == back
        assert Payment.objects.count() == 1

    def test_sin_next_se_sigue_yendo_al_detalle(self, panel_client, issued_invoice_a):
        """El cobro desde la ficha no cambia de comportamiento."""
        response = panel_client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '20,00', 'method': Payment.Method.CARD},
        )

        assert response.url == reverse('billing:invoice-detail', args=[issued_invoice_a.pk])

    def test_un_error_vuelve_donde_se_estaba_trabajando(self, panel_client, issued_invoice_a):
        """El mensaje hay que leerlo en la pantalla desde la que se cobró."""
        back = f'{LIST_URL}?cobro=pending'

        response = panel_client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '500,00', 'method': Payment.Method.CARD, 'next': back},
        )

        assert response.url == back
        assert not Payment.objects.exists()
        mensajes = [str(m) for m in response.wsgi_request._messages]
        assert any('no puede superar' in m for m in mensajes)

    @pytest.mark.parametrize('hostil', [
        'https://evil.example.com/roba',
        '//evil.example.com/roba',
        'http://evil.example.com',
    ])
    def test_no_redirige_fuera_del_sitio(self, panel_client, issued_invoice_a, hostil):
        """`next` lo teclea la URL: seguirlo a ciegas sería una redirección abierta."""
        response = panel_client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '20,00', 'method': Payment.Method.CARD, 'next': hostil},
        )

        assert response.url == reverse('billing:invoice-detail', args=[issued_invoice_a.pk])

    def test_el_fragmento_tampoco_arrastra_un_destino_hostil(
        self, panel_client, issued_invoice_a,
    ):
        body = panel_client.get(
            _form_url(issued_invoice_a, next='https://evil.example.com')
        ).content.decode()

        assert 'evil.example.com' not in body


# ---------------------------------------------------------------------------
# Coste
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_el_boton_no_cuesta_consultas(
    django_assert_max_num_queries, panel_client, clinic_a, patient_a, visit_a, service_a,
):
    """`amount_due` sale de la anotación de `with_collection()`, no de la property.

    Leerlo de la property dispararía un agregado por fila, que es justo lo que el
    listado lleva evitando desde que existe.
    """
    for _ in range(5):
        invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
        invoice.issue()

    with django_assert_max_num_queries(15):
        response = panel_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.content.decode().count('Cobrar') >= 5
