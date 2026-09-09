"""El bloque económico del panel: lo que enseña es dinero, no una estimación.

Antes el panel sumaba el precio de catálogo de las citas completadas del mes.
Eso no era dinero cobrado —una cita atendida no es una factura, y una factura no
es un cobro— y además leía el precio *vivo* del servicio, así que subir la tarifa
reescribía hacia atrás lo que se había «ingresado» en enero.

Lo que se defiende aquí es que cada cifra mide lo que dice medir:

- «cobrado» son `Payment`, por su `paid_at`,
- «facturado» son facturas emitidas, por su `issued_at`,
- «por cobrar» es el saldo vivo de las emitidas, sin acotar al mes,

y que ninguna de las tres cruza la frontera de la clínica.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from billing.metrics import MIN_BAR_PCT, dashboard_revenue
from billing.models import PatientInvoice, Payment
from clinical.models import PerformedProcedure

DASHBOARD_URL = reverse('core:dashboard')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def today():
    return timezone.localdate()


def _pay(invoice, amount, *, when=None):
    """Un cobro, opcionalmente con fecha puesta a mano."""
    payment = Payment.objects.create(
        invoice=invoice, amount=Decimal(amount), method=Payment.Method.CARD,
    )
    if when is not None:
        # `paid_at` está congelado tras el alta, así que la fecha se corrige por
        # SQL. Es una consulta de test, no un camino de la aplicación.
        Payment.all_objects.filter(pk=payment.pk).update(paid_at=when)
        payment.refresh_from_db()
    return payment


def _issued_invoice(clinic, patient, visit, service):
    """Otra factura emitida, con un procedimiento dentro (50.00)."""
    invoice = PatientInvoice.objects.create(clinic=clinic, patient=patient)
    invoice.add_procedure(
        PerformedProcedure.objects.create(visit=visit, service=service)
    )
    return invoice.issue()


# ---------------------------------------------------------------------------
# Lo cobrado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoCobradoEsDineroQueEntro:
    def test_suma_los_cobros_del_mes(self, admin_user, issued_invoice_a, today):
        _pay(issued_invoice_a, '20.00')
        _pay(issued_invoice_a, '15.00')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['collected'] == Decimal('35.00')

    def test_un_cobro_del_mes_pasado_queda_fuera(self, admin_user, issued_invoice_a, today):
        """El corte es el mes natural, no «los últimos treinta días»."""
        last_month = timezone.now() - timedelta(days=40)
        _pay(issued_invoice_a, '20.00', when=last_month)

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['collected'] == Decimal('0.00')

    def test_no_ve_los_cobros_de_otra_clinica(
        self, admin_user, admin_user_b, clinic_b, patient_b, issued_invoice_a, today,
    ):
        _pay(issued_invoice_a, '20.00')

        revenue_b = dashboard_revenue(admin_user_b, today)

        assert revenue_b['collected'] == Decimal('0.00')
        assert dashboard_revenue(admin_user, today)['collected'] == Decimal('20.00')

    def test_sin_cobros_el_panel_no_se_rompe(self, admin_user, today):
        """El caso del primer día: todo a cero y ninguna división entre cero."""
        revenue = dashboard_revenue(admin_user, today)

        assert revenue['collected'] == Decimal('0.00')
        assert revenue['best_day'] is None
        assert revenue['delta_pct'] is None
        assert all(point['pct'] == 0 for point in revenue['series'])


# ---------------------------------------------------------------------------
# La serie diaria
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLaSerieDiaria:
    def test_hay_un_punto_por_cada_dia_del_mes(self, admin_user, today):
        """También los días vacíos y los que aún no han llegado.

        Saltarse los huecos comprimiría el tiempo y haría parecer constante lo
        que fue a rachas.
        """
        import calendar

        revenue = dashboard_revenue(admin_user, today)
        days_in_month = calendar.monthrange(today.year, today.month)[1]

        assert len(revenue['series']) == days_in_month
        assert revenue['series'][0]['date'] == today.replace(day=1)
        assert revenue['series'][-1]['date'].day == days_in_month

    def test_dos_cobros_del_mismo_dia_se_suman_en_una_barra(
        self, admin_user, issued_invoice_a, today,
    ):
        _pay(issued_invoice_a, '20.00')
        _pay(issued_invoice_a, '10.00')

        revenue = dashboard_revenue(admin_user, today)
        point = next(p for p in revenue['series'] if p['is_today'])

        assert point['amount'] == Decimal('30.00')
        assert point['pct'] == 100  # es el único día con cobros: es el máximo

    def test_la_altura_se_normaliza_contra_el_mejor_dia(
        self, admin_user, clinic_a, patient_a, visit_a, service_a, today,
    ):
        """La barra dice cuánto fue ese día *comparado con el mejor*, no el total."""
        if today.day < 3:
            pytest.skip('hacen falta dos días distintos ya pasados en el mes')

        invoice = _issued_invoice(clinic_a, patient_a, visit_a, service_a)
        day_one = timezone.now().replace(hour=12) - timedelta(days=2)
        _pay(invoice, '40.00', when=day_one)
        _pay(invoice, '10.00')

        revenue = dashboard_revenue(admin_user, today)
        best = next(p for p in revenue['series'] if p['date'] == day_one.date())
        small = next(p for p in revenue['series'] if p['is_today'])

        assert best['pct'] == 100
        assert small['pct'] == 25
        assert revenue['best_day']['date'] == day_one.date()

    def test_un_dia_con_muy_poco_sigue_viendose(
        self, admin_user, clinic_a, patient_a, visit_a, service_a, today,
    ):
        """Un día que cobró algo cobró algo: no puede pintarse como uno vacío."""
        if today.day < 3:
            pytest.skip('hacen falta dos días distintos ya pasados en el mes')

        invoice = _issued_invoice(clinic_a, patient_a, visit_a, service_a)
        _pay(invoice, '49.50', when=timezone.now().replace(hour=12) - timedelta(days=2))
        _pay(invoice, '0.50')

        revenue = dashboard_revenue(admin_user, today)
        small = next(p for p in revenue['series'] if p['is_today'])

        # 0,50 sobre 49,50 es un 1 % — una barra de medio píxel, que se leería
        # igual que un día sin cobrar. El suelo la levanta hasta verse.
        assert small['amount'] == Decimal('0.50')
        assert small['pct'] == MIN_BAR_PCT


# ---------------------------------------------------------------------------
# La comparación con el mes pasado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLaComparacion:
    def test_sin_cobros_el_mes_pasado_no_hay_porcentaje(
        self, admin_user, issued_invoice_a, today,
    ):
        """De 0 a 300 € no es «un 100 % más»: no hay nada con qué comparar."""
        _pay(issued_invoice_a, '20.00')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['previous_collected'] == Decimal('0.00')
        assert revenue['delta_pct'] is None
        assert revenue['delta_abs'] is None

    def test_compara_contra_el_mismo_tramo_del_mes_pasado(
        self, admin_user, clinic_a, patient_a, visit_a, service_a, today,
    ):
        """Tramo contra tramo, no medio mes contra un mes entero."""
        invoice = _issued_invoice(clinic_a, patient_a, visit_a, service_a)
        previous_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        moment = timezone.make_aware(
            timezone.datetime(previous_first.year, previous_first.month, 1, 12, 0)
        )
        _pay(invoice, '40.00', when=moment)
        _pay(invoice, '10.00')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['previous_collected'] == Decimal('40.00')
        assert revenue['delta_pct'] == -75
        assert revenue['delta_abs'] == 75


# ---------------------------------------------------------------------------
# Lo facturado y lo que falta por cobrar
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLoFacturadoYLoPendiente:
    def test_lo_facturado_mide_por_la_emision(self, admin_user, issued_invoice_a, today):
        revenue = dashboard_revenue(admin_user, today)

        assert revenue['billed'] == Decimal('50.00')

    def test_un_borrador_no_es_deuda_de_nadie(
        self, admin_user, draft_invoice_a, procedure_a, today,
    ):
        """Un borrador todavía puede cambiar de importe: no se debe."""
        draft_invoice_a.add_procedure(procedure_a)

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['billed'] == Decimal('0.00')
        assert revenue['pending_amount'] == Decimal('0.00')
        assert revenue['unpaid_count'] == 0

    def test_una_anulada_deja_de_deberse(self, admin_user, issued_invoice_a, today):
        issued_invoice_a.void(reason='Error en el importe')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['billed'] == Decimal('0.00')
        assert revenue['pending_amount'] == Decimal('0.00')
        assert revenue['unpaid_count'] == 0

    def test_una_cobrada_a_medias_sigue_contando_como_pendiente(
        self, admin_user, issued_invoice_a, today,
    ):
        """Lo que interesa es a quién reclamar, y a medias se reclama igual."""
        _pay(issued_invoice_a, '20.00')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['pending_amount'] == Decimal('30.00')
        assert revenue['unpaid_count'] == 1

    def test_una_cobrada_del_todo_desaparece_del_pendiente(
        self, admin_user, issued_invoice_a, today,
    ):
        _pay(issued_invoice_a, '50.00')

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['pending_amount'] == Decimal('0.00')
        assert revenue['unpaid_count'] == 0

    def test_el_pendiente_no_se_acota_al_mes(
        self, admin_user, issued_invoice_a, today,
    ):
        """Lo que se debe se debe venga de cuando venga."""
        old = timezone.now() - timedelta(days=90)
        PatientInvoice.all_objects.filter(pk=issued_invoice_a.pk).update(issued_at=old)

        revenue = dashboard_revenue(admin_user, today)

        assert revenue['billed'] == Decimal('0.00')          # no se emitió este mes
        assert revenue['pending_amount'] == Decimal('50.00')  # pero se sigue debiendo

    def test_lo_sin_facturar_es_otra_cosa(
        self, admin_user, procedure_a, today,
    ):
        """Trabajo hecho que aún no es factura: un documento por hacer."""
        revenue = dashboard_revenue(admin_user, today)

        assert revenue['unbilled_count'] == 1
        assert revenue['unbilled_amount'] == Decimal('50.00')
        assert revenue['pending_amount'] == Decimal('0.00')


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestElPanel:
    def test_el_panel_pinta_lo_cobrado(self, panel_client, issued_invoice_a):
        _pay(issued_invoice_a, '20.00')

        response = panel_client.get(DASHBOARD_URL)

        assert response.status_code == 200
        assert response.context['revenue']['collected'] == Decimal('20.00')
        assert '20,00\xa0€' in response.content.decode()

    def test_solo_el_dia_de_hoy_va_marcado(self, panel_client, issued_invoice_a, today):
        """El marcador existe en las 30 columnas y solo se colorea en una.

        El hueco está siempre para que la columna de hoy mida lo mismo que las
        demás: si el subrayado solo existiera bajo el día actual, esa barra
        arrancaría más arriba y la gráfica mentiría justo sobre el día que más
        se mira.
        """
        import calendar

        _pay(issued_invoice_a, '20.00')
        body = panel_client.get(DASHBOARD_URL).content.decode()
        days_in_month = calendar.monthrange(today.year, today.month)[1]

        assert body.count('mt-1 block h-1 rounded-full') == days_in_month
        assert body.count('h-1 rounded-full bg-brand-fg') == 1
        assert f'{today.day} ' in body  # el title de hoy lleva su fecha
        assert '(hoy)' in body

    def test_el_enlace_de_por_cobrar_lleva_a_esas_facturas(
        self, panel_client, issued_invoice_a,
    ):
        """La cifra cuenta impagadas Y parciales, y el enlace tiene que traer las dos."""
        from billing.filters import COLLECTION_PENDING, InvoiceFilters, invoices_for

        _pay(issued_invoice_a, '20.00')
        response = panel_client.get(DASHBOARD_URL)
        revenue = response.context['revenue']

        assert f'?cobro={COLLECTION_PENDING}' in response.content.decode()
        listed = InvoiceFilters.from_query({'cobro': COLLECTION_PENDING}).apply(
            invoices_for(response.wsgi_request.user)
        )
        assert listed.count() == revenue['unpaid_count'] == 1

    def test_no_crece_en_consultas_con_el_numero_de_cobros(
        self, django_assert_max_num_queries, panel_client,
        clinic_a, patient_a, visit_a, service_a,
    ):
        """Todo va agregado en la base de datos: cinco cobros cuestan lo mismo que uno."""
        for _ in range(5):
            invoice = _issued_invoice(clinic_a, patient_a, visit_a, service_a)
            _pay(invoice, '25.00')

        with django_assert_max_num_queries(15):
            response = panel_client.get(DASHBOARD_URL)

        assert response.status_code == 200

    def test_el_panel_de_otra_clinica_no_ve_este_dinero(
        self, client, admin_user_b, issued_invoice_a,
    ):
        _pay(issued_invoice_a, '20.00')
        client.force_login(admin_user_b)

        response = client.get(DASHBOARD_URL)

        assert response.context['revenue']['collected'] == Decimal('0.00')
        assert response.context['revenue']['pending_amount'] == Decimal('0.00')
