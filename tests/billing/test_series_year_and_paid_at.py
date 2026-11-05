"""El año de las series es el de la clínica, y la fecha de un cobro tiene sentido.

Dos arreglos pequeños sobre lo que ya había:

- Las series (`F-AAAA-…`, `R-AAAA-…`) contaban el año en UTC. A medianoche del
  31 de diciembre en Madrid, una factura con fecha de enero tomaba número del año
  que acababa.
- `Payment.paid_at` aceptaba cualquier fecha: el futuro (dinero que aún no ha
  entrado) o un día en que la factura ni existía.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from billing.managers import clinic_zone, next_invoice_number, next_receipt_number, series_year
from billing.models import PatientInvoice, Payment, ReceiptSequence


def _utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    """Fija `timezone.now()`. Hay que pedirla ANTES que las fixtures que crean facturas."""
    def set_now(moment):
        monkeypatch.setattr(timezone, 'now', lambda: moment)
    return set_now


# ---------------------------------------------------------------------------
# El año de la serie
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheSeriesYearIsTheClinicsYear:
    def test_after_midnight_in_madrid_it_is_already_the_new_year(self, clinic_a):
        # 23:30 UTC del 31/12 son las 00:30 del 1/1 en Madrid (CET, UTC+1).
        moment = _utc(2026, 12, 31, 23, 30)

        assert clinic_a.timezone == 'Europe/Madrid'
        assert series_year(clinic_a, moment) == 2027
        assert next_invoice_number(clinic_a, moment) == 'F-2027-00001'
        assert next_receipt_number(clinic_a, moment) == 'R-2027-00001'

    def test_before_midnight_in_madrid_it_is_still_the_old_year(self, clinic_a):
        moment = _utc(2026, 12, 31, 22, 30)  # 23:30 en Madrid

        assert series_year(clinic_a, moment) == 2026
        assert next_invoice_number(clinic_a, moment) == 'F-2026-00001'

    def test_another_timezone_counts_in_its_own_calendar(self, clinic_a):
        clinic_a.timezone = 'America/Mexico_City'  # UTC-6: aún es 2026
        moment = _utc(2027, 1, 1, 3, 0)

        assert series_year(clinic_a, moment) == 2026

    @pytest.mark.parametrize('bad', ['', 'Mars/Olympus', 'no es una zona'])
    def test_a_broken_timezone_does_not_stop_invoicing(self, clinic_a, bad):
        clinic_a.timezone = bad

        assert clinic_zone(clinic_a) is not None
        assert series_year(clinic_a, _utc(2026, 6, 1)) == 2026

    def test_the_number_and_the_issue_date_fall_in_the_same_year(
        self, clock, draft_invoice_a, procedure_a
    ):
        """Emitida a las 00:30 de Madrid del 1 de enero: número y fecha de 2027."""
        clock(_utc(2026, 12, 31, 23, 30))
        draft_invoice_a.add_procedure(procedure_a)

        invoice = draft_invoice_a.issue()

        assert invoice.number == 'F-2027-00001'
        assert timezone.localtime(invoice.issued_at).year == 2027

    def test_a_new_year_restarts_the_count(self, clock, clinic_a, patient_a, procedure_a):
        clock(_utc(2026, 12, 31, 22, 0))
        first = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        first.add_procedure(procedure_a)
        assert first.issue().number == 'F-2026-00001'

        clock(_utc(2026, 12, 31, 23, 30))
        assert next_invoice_number(clinic_a, timezone.now()) == 'F-2027-00001'

    def test_the_receipt_series_uses_the_registration_year(
        self, clock, issued_invoice_a
    ):
        clock(_utc(2026, 12, 31, 23, 30))

        payment = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('10.00'),
            method=Payment.Method.CARD, paid_at=_utc(2026, 12, 31, 23, 0),
        )

        assert payment.receipt_number == 'R-2027-00001'


# ---------------------------------------------------------------------------
# La fecha del cobro
# ---------------------------------------------------------------------------

def _issued_on(invoice, moment):
    """Retrasa la emisión de una factura ya emitida. `issued_at` está congelado,
    así que la fecha se corrige por debajo, como hacen los tests del panel."""
    PatientInvoice.all_objects.filter(pk=invoice.pk).update(issued_at=moment)
    invoice.refresh_from_db()
    return invoice


@pytest.mark.django_db
class TestThePaidDateMakesSense:
    def test_a_future_date_is_rejected(self, issued_invoice_a):
        with pytest.raises(ValidationError) as caught:
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('10.00'),
                method=Payment.Method.CARD,
                paid_at=timezone.now() + timedelta(days=1),
            )

        assert 'futuro' in str(caught.value)
        assert 'paid_at' in caught.value.message_dict

    def test_a_date_before_the_invoice_existed_is_rejected(self, issued_invoice_a):
        invoice = _issued_on(issued_invoice_a, _utc(2026, 3, 10, 12, 0))

        with pytest.raises(ValidationError) as caught:
            Payment.objects.create(
                invoice=invoice, amount=Decimal('10.00'), method=Payment.Method.CARD,
                paid_at=_utc(2026, 3, 9, 12, 0),
            )

        assert 'anterior' in str(caught.value)
        assert '10/03/2026' in str(caught.value)

    def test_earlier_the_same_day_is_fine(self, issued_invoice_a):
        """Cobrar en consulta a las 10:00 y emitir la factura a las 12:00 es lo normal."""
        invoice = _issued_on(issued_invoice_a, _utc(2026, 3, 10, 12, 0))

        payment = Payment.objects.create(
            invoice=invoice, amount=Decimal('10.00'), method=Payment.Method.CARD,
            paid_at=_utc(2026, 3, 10, 9, 0),  # 10:00 en Madrid, el mismo día
        )

        assert payment.pk

    def test_a_late_registration_of_an_old_payment_is_fine(self, issued_invoice_a):
        invoice = _issued_on(issued_invoice_a, _utc(2026, 3, 10, 12, 0))

        payment = Payment.objects.create(
            invoice=invoice, amount=Decimal('10.00'), method=Payment.Method.CASH,
            paid_at=_utc(2026, 3, 20, 12, 0),
        )

        assert payment.pk

    def test_the_day_is_counted_in_the_clinics_calendar(self, issued_invoice_a):
        """23:30 UTC del 9/3 ya es el 10/3 en Madrid: el mismo día de la emisión."""
        invoice = _issued_on(issued_invoice_a, _utc(2026, 3, 10, 12, 0))

        payment = Payment.objects.create(
            invoice=invoice, amount=Decimal('10.00'), method=Payment.Method.CARD,
            paid_at=_utc(2026, 3, 9, 23, 30),
        )

        assert payment.pk

    def test_a_rejected_date_does_not_burn_a_receipt_number(self, issued_invoice_a):
        with pytest.raises(ValidationError):
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('10.00'),
                method=Payment.Method.CARD,
                paid_at=timezone.now() + timedelta(days=1),
            )

        assert not ReceiptSequence.objects.exists()

    def test_from_the_panel_it_comes_out_as_a_message(self, client, admin_user, issued_invoice_a):
        client.force_login(admin_user)
        tomorrow = timezone.localtime() + timedelta(days=1)

        response = client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '10.00', 'method': 'card',
             'paid_at': tomorrow.strftime('%Y-%m-%dT%H:%M')},
            follow=True,
        )

        assert response.status_code == 200
        assert 'no puede estar en el futuro' in response.content.decode()
        assert Payment.objects.count() == 0
        assert not ReceiptSequence.objects.exists()
