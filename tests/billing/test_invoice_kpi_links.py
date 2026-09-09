"""Los KPIs de la cabecera llevan a lo que cuentan.

Una tarjeta es un enlace solo cuando la lista a la que va contiene EXACTAMENTE
lo que la cifra cuenta. Pinchar un número y ver otra cosa es peor que no poder
pincharlo, así que dos de las cinco no llevan a ningún sitio a propósito: el
recuento de facturas ya está mirando su propia lista, y lo pendiente de facturar
cuenta procedimientos, para los que no hay listado.

Las tres que sí llevan conservan el resto de filtros y alternan: si su filtro ya
está puesto, el mismo clic lo quita.
"""
import re

import pytest
from django.urls import reverse

from billing.filters import COLLECTION_PENDING

LIST_URL = reverse('billing:invoice-list')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


def _card(body, title):
    """La etiqueta de apertura de la tarjeta cuyo título se pasa.

    Busca hacia atrás desde el título la última apertura de `<a>` o `<div>` que
    lleve el `rounded-3xl` de las tarjetas: eso distingue la tarjeta de los
    envoltorios internos, y sirve igual para las clicables y las que no lo son.
    """
    i = body.index(f'>{title}<')
    opens = [m for m in re.finditer(r'<(a|div)\s[^>]*rounded-3xl[^>]*>', body[:i])]
    assert opens, f'no se encontró la tarjeta de {title!r}'
    return opens[-1].group(0)


@pytest.mark.django_db
class TestQueTarjetasLlevanAAlgunSitio:
    def test_facturado_lleva_a_las_emitidas(self, panel_client, issued_invoice_a):
        card = _card(panel_client.get(LIST_URL).content.decode(), 'Total facturado')

        assert card.startswith('<a ')
        assert 'status=issued' in card

    def test_ticket_medio_lleva_al_mismo_conjunto(self, panel_client, issued_invoice_a):
        """La media sale de las emitidas: es la lista que responde «¿de dónde?»."""
        card = _card(panel_client.get(LIST_URL).content.decode(), 'Ticket medio')

        assert card.startswith('<a ')
        assert 'status=issued' in card

    def test_pendiente_de_cobro_lleva_a_las_que_tienen_saldo(self, panel_client, payment_a):
        card = _card(panel_client.get(LIST_URL).content.decode(), 'Pendiente de cobro')

        assert card.startswith('<a ')
        assert f'cobro={COLLECTION_PENDING}' in card

    def test_el_recuento_de_facturas_no_es_un_enlace(self, panel_client, issued_invoice_a):
        """Ya se está mirando su lista: no hay a dónde ir."""
        card = _card(panel_client.get(LIST_URL).content.decode(), 'Facturas')

        assert not card.startswith('<a ')

    def test_pendiente_de_facturar_no_es_un_enlace(self, panel_client, procedure_a):
        """Cuenta procedimientos, y no hay listado de procedimientos sin facturar."""
        card = _card(panel_client.get(LIST_URL).content.decode(), 'Pendiente de facturar')

        assert not card.startswith('<a ')


@pytest.mark.django_db
class TestElClicAlterna:
    def test_con_el_filtro_puesto_el_clic_lo_quita(self, panel_client, issued_invoice_a):
        """Si no, pinchar una tarjeta ya activa no haría nada y parecería rota."""
        body = panel_client.get(LIST_URL, {'status': 'issued'}).content.decode()
        card = _card(body, 'Total facturado')

        assert 'status=issued' not in card

    def test_la_tarjeta_activa_se_distingue(self, panel_client, issued_invoice_a):
        body = panel_client.get(LIST_URL, {'status': 'issued'}).content.decode()
        classes = re.search(r'class="([^"]*)"', _card(body, 'Total facturado')).group(1)

        assert 'ring-success' in classes.split()
        assert 'ring-line' not in classes.split()

    def test_el_cobro_pendiente_tambien_alterna(self, panel_client, payment_a):
        body = panel_client.get(LIST_URL, {'cobro': COLLECTION_PENDING}).content.decode()
        card = _card(body, 'Pendiente de cobro')

        assert f'cobro={COLLECTION_PENDING}' not in card


@pytest.mark.django_db
class TestNoSePierdenLosFiltros:
    def test_conserva_lo_que_ya_estaba_puesto(self, panel_client, issued_invoice_a):
        """Acotar por una tarjeta no puede tirar el paciente ni las fechas."""
        body = panel_client.get(
            LIST_URL, {'q': 'John', 'desde': '2026-01-01'},
        ).content.decode()
        card = _card(body, 'Total facturado')

        assert 'q=John' in card
        assert 'desde=2026-01-01' in card
        assert 'status=issued' in card


@pytest.mark.django_db
def test_la_tarjeta_dice_a_cuantas_lleva(panel_client, payment_a):
    """El número de la tarjeta es el de las facturas que hay al otro lado.

    Que ese recuento coincida de verdad con la lista lo fija
    `test_invoice_panel.test_con_saldo_cuadra_con_el_kpi_de_impagadas`; aquí solo
    se comprueba que la tarjeta lo enseña.
    """
    body = panel_client.get(LIST_URL).content.decode()

    assert '1 factura con saldo pendiente' in body
