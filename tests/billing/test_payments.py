"""Cobros: un recibo nace confirmado, congelado y para siempre.

La factura dice qué se debe; el pago dice que entró el dinero. Son dos hechos
distintos, y por eso el pago tiene su propia serie, su propio número y su propia
copia de lo que necesita para leerse solo.

Lo que se defiende aquí: solo se cobra una factura emitida, nunca más de lo que
debe, el recibo no se corrige ni se borra, y el estado de cobro de una factura se
**deriva** de sus pagos en vez de almacenarse (un campo desincronizado con el
dinero real sería peor que no tenerlo).
"""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from billing.exceptions import (
    InvoiceHasPayments,
    InvoiceNotPayable,
    Overpayment,
    PaymentFrozen,
)
from billing.models import PatientInvoice, Payment, ReceiptSequence
from core.managers import ProtectedRecordError


@pytest.fixture
def payment_a(db, issued_invoice_a):
    """Cobro parcial de 20.00 sobre una factura de 50.00."""
    return Payment.objects.create(
        invoice=issued_invoice_a,
        amount=Decimal('20.00'),
        method=Payment.Method.CARD,
    )


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAReceiptIsBornConfirmed:
    def test_registering_a_payment_takes_a_receipt_number(self, payment_a):
        year = payment_a.paid_at.year
        assert payment_a.receipt_number == f'R-{year}-00001'

    def test_the_clinic_comes_from_the_invoice(self, payment_a, clinic_a):
        assert payment_a.clinic_id == clinic_a.pk

    def test_the_receipt_copies_what_it_needs_to_be_read_alone(
        self, payment_a, issued_invoice_a, patient_a
    ):
        stored = Payment.objects.get(pk=payment_a.pk)
        assert stored.frozen_invoice_number == issued_invoice_a.number
        assert stored.frozen_patient_name == str(patient_a)

    def test_several_partial_payments_are_allowed(self, issued_invoice_a, payment_a):
        second = Payment.objects.create(
            invoice=issued_invoice_a,
            amount=Decimal('30.00'),
            method=Payment.Method.CASH,
        )

        assert issued_invoice_a.payments.count() == 2
        assert second.receipt_number != payment_a.receipt_number

    def test_a_payment_without_an_invoice_is_rejected(self, db):
        with pytest.raises(ValidationError):
            Payment.objects.create(amount=Decimal('10.00'), method=Payment.Method.CASH)

        assert Payment.all_objects.count() == 0


# ---------------------------------------------------------------------------
# Solo se cobra una factura emitida
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnlyAnIssuedInvoiceIsCharged:
    def test_a_draft_cannot_be_charged(self, draft_invoice_a):
        """Un borrador todavía puede cambiar de importe."""
        with pytest.raises(InvoiceNotPayable):
            Payment.objects.create(
                invoice=draft_invoice_a,
                amount=Decimal('10.00'),
                method=Payment.Method.CASH,
            )

        assert Payment.all_objects.count() == 0

    def test_a_voided_invoice_cannot_be_charged(self, issued_invoice_a):
        issued_invoice_a.void('Error')

        with pytest.raises(InvoiceNotPayable):
            Payment.objects.create(
                invoice=issued_invoice_a,
                amount=Decimal('10.00'),
                method=Payment.Method.CASH,
            )

    def test_the_receipt_series_is_not_burnt_by_a_rejected_payment(
        self, draft_invoice_a, clinic_a
    ):
        """El número se toma después de validar: un intento fallido no gasta serie."""
        with pytest.raises(InvoiceNotPayable):
            Payment.objects.create(
                invoice=draft_invoice_a,
                amount=Decimal('10.00'),
                method=Payment.Method.CASH,
            )

        assert not ReceiptSequence.objects.filter(clinic=clinic_a).exists()


# ---------------------------------------------------------------------------
# Sobrepago
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNoOneCollectsMoreThanIsOwed:
    def test_a_payment_above_the_total_is_rejected(self, issued_invoice_a):
        with pytest.raises(Overpayment):
            Payment.objects.create(
                invoice=issued_invoice_a,
                amount=Decimal('50.01'),
                method=Payment.Method.CARD,
            )

        assert Payment.all_objects.count() == 0

    def test_the_sum_of_partials_cannot_exceed_the_total(
        self, issued_invoice_a, payment_a
    ):
        """20 + 30.01 sobre una factura de 50: el segundo cobro sobra."""
        with pytest.raises(Overpayment):
            Payment.objects.create(
                invoice=issued_invoice_a,
                amount=Decimal('30.01'),
                method=Payment.Method.BIZUM,
            )

        assert issued_invoice_a.amount_collected == Decimal('20.00')

    def test_paying_exactly_what_is_left_is_allowed(self, issued_invoice_a, payment_a):
        Payment.objects.create(
            invoice=issued_invoice_a,
            amount=Decimal('30.00'),
            method=Payment.Method.BIZUM,
        )

        assert issued_invoice_a.amount_collected == Decimal('50.00')
        assert issued_invoice_a.amount_due == Decimal('0.00')

    def test_a_fully_paid_invoice_takes_no_more_money(
        self, issued_invoice_a, payment_a
    ):
        Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('30.00'),
            method=Payment.Method.BIZUM,
        )

        with pytest.raises(Overpayment):
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('0.01'),
                method=Payment.Method.CASH,
            )

    def test_a_zero_payment_is_rejected_by_the_database(self, issued_invoice_a):
        """Un recibo de cero euros no prueba nada."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('0.00'),
                method=Payment.Method.CASH,
            )

    def test_a_negative_payment_is_rejected_by_the_database(self, issued_invoice_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('-10.00'),
                method=Payment.Method.CASH,
            )


# ---------------------------------------------------------------------------
# Congelado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAReceiptIsNeverRewritten:
    def test_the_amount_cannot_be_edited(self, payment_a):
        payment_a.amount = Decimal('45.00')

        with pytest.raises(PaymentFrozen):
            payment_a.save()

        assert Payment.objects.get(pk=payment_a.pk).amount == Decimal('20.00')

    def test_the_method_cannot_be_edited(self, payment_a):
        payment_a.method = Payment.Method.CASH

        with pytest.raises(PaymentFrozen):
            payment_a.save()

    def test_the_receipt_number_cannot_be_edited(self, payment_a):
        payment_a.receipt_number = 'R-1999-00001'

        with pytest.raises(PaymentFrozen):
            payment_a.save()

    def test_the_invoice_cannot_be_swapped(
        self, payment_a, clinic_a, patient_a, visit_a, service_a
    ):
        from clinical.models import PerformedProcedure

        other = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        other.add_procedure(
            PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        )
        other.issue()

        payment_a.refresh_from_db()
        payment_a.invoice = other

        with pytest.raises(PaymentFrozen):
            payment_a.save()

    def test_the_paid_date_cannot_be_edited(self, payment_a):
        from datetime import timedelta

        payment_a.paid_at = payment_a.paid_at - timedelta(days=30)

        with pytest.raises(PaymentFrozen):
            payment_a.save()

    def test_raising_the_catalog_price_does_not_move_the_receipt(
        self, payment_a, service_a
    ):
        service_a.price = Decimal('65.00')
        service_a.save()

        payment_a.refresh_from_db()
        assert payment_a.amount == Decimal('20.00')


# ---------------------------------------------------------------------------
# Borrado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAReceiptIsNeverDeleted:
    def test_a_payment_cannot_be_deleted(self, payment_a):
        with pytest.raises(ProtectedRecordError):
            payment_a.delete()

        assert Payment.objects.filter(pk=payment_a.pk).exists()

    def test_a_queryset_delete_respects_the_veto(self, payment_a):
        with pytest.raises(ProtectedRecordError):
            Payment.objects.filter(pk=payment_a.pk).delete()

        assert Payment.objects.filter(pk=payment_a.pk).exists()

    def test_can_be_deleted_is_always_false(self, payment_a):
        assert payment_a.can_be_deleted() is False


# ---------------------------------------------------------------------------
# La serie de recibos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheReceiptSeriesIsItsOwn:
    def test_receipts_are_correlative_within_a_clinic(self, issued_invoice_a):
        first = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('20.00'),
            method=Payment.Method.CARD,
        )
        second = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('30.00'),
            method=Payment.Method.CASH,
        )

        year = first.paid_at.year
        assert first.receipt_number == f'R-{year}-00001'
        assert second.receipt_number == f'R-{year}-00002'

    def test_the_receipt_series_is_separate_from_the_invoice_series(
        self, issued_invoice_a, payment_a
    ):
        """Emitir facturas no consume números de recibo, ni al revés."""
        assert payment_a.receipt_number.startswith('R-')
        assert issued_invoice_a.number.startswith('F-')
        assert payment_a.receipt_number[1:] == issued_invoice_a.number[1:]

    def test_the_counter_lives_per_clinic_and_year(self, payment_a, clinic_a):
        sequence = ReceiptSequence.objects.get(
            clinic=clinic_a, year=payment_a.paid_at.year
        )
        assert sequence.last_value == 1

    def test_the_same_receipt_number_cannot_repeat_within_a_clinic(
        self, issued_invoice_a, payment_a
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.all_objects.create(
                invoice=issued_invoice_a, clinic=issued_invoice_a.clinic,
                receipt_number=payment_a.receipt_number,
                amount=Decimal('10.00'), method=Payment.Method.CASH,
            )


# ---------------------------------------------------------------------------
# Anular una factura cobrada
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAChargedInvoiceIsNotVoided:
    def test_voiding_an_invoice_with_payments_is_rejected(
        self, issued_invoice_a, payment_a
    ):
        with pytest.raises(InvoiceHasPayments):
            issued_invoice_a.void('Error')

        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.is_issued

    def test_the_procedures_stay_charged(
        self, issued_invoice_a, payment_a, procedure_a
    ):
        """Un veto no se degrada: no libera nada a medias."""
        with pytest.raises(InvoiceHasPayments):
            issued_invoice_a.void('Error')

        procedure_a.refresh_from_db()
        assert procedure_a.invoice_id == issued_invoice_a.pk

    def test_an_uncharged_invoice_is_still_voidable(self, issued_invoice_a):
        issued_invoice_a.void('Error')

        assert issued_invoice_a.is_void


# ---------------------------------------------------------------------------
# Estado de cobro: derivado, nunca almacenado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestThePaymentStateIsDerived:
    def test_an_invoice_with_no_payments_is_unpaid(self, issued_invoice_a):
        invoice = PatientInvoice.objects.with_collection().get(pk=issued_invoice_a.pk)

        assert invoice.amount_collected == Decimal('0.00')
        assert invoice.payment_state == PatientInvoice.PaymentState.UNPAID

    def test_a_partially_paid_invoice_is_partial(self, issued_invoice_a, payment_a):
        invoice = PatientInvoice.objects.with_collection().get(pk=issued_invoice_a.pk)

        assert invoice.amount_collected == Decimal('20.00')
        assert invoice.payment_state == PatientInvoice.PaymentState.PARTIAL

    def test_a_fully_paid_invoice_is_paid(self, issued_invoice_a, payment_a):
        Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('30.00'),
            method=Payment.Method.BIZUM,
        )

        invoice = PatientInvoice.objects.with_collection().get(pk=issued_invoice_a.pk)
        assert invoice.amount_collected == Decimal('50.00')
        assert invoice.payment_state == PatientInvoice.PaymentState.PAID

    def test_the_property_agrees_with_the_annotation(self, issued_invoice_a, payment_a):
        """La instancia suelta y el listado dicen lo mismo."""
        annotated = PatientInvoice.objects.with_collection().get(pk=issued_invoice_a.pk)
        issued_invoice_a.refresh_from_db()

        assert issued_invoice_a.payment_state == annotated.payment_state
        assert issued_invoice_a.amount_collected == annotated.amount_collected

    def test_the_listing_resolves_every_invoice_in_one_query(
        self, django_assert_num_queries, clinic_a, patient_a, visit_a, service_a
    ):
        """El estado de cobro no puede costar una consulta por fila."""
        from clinical.models import PerformedProcedure

        for _ in range(3):
            invoice = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
            invoice.add_procedure(
                PerformedProcedure.objects.create(visit=visit_a, service=service_a)
            )
            invoice.issue()
            Payment.objects.create(
                invoice=invoice, amount=Decimal('25.00'), method=Payment.Method.CARD,
            )

        with django_assert_num_queries(1):
            states = [
                (invoice.amount_collected, invoice.payment_state)
                for invoice in PatientInvoice.objects.with_collection()
            ]

        assert states == [(Decimal('25.00'), PatientInvoice.PaymentState.PARTIAL)] * 3

    def test_no_stored_field_backs_the_state(self):
        """Si un día alguien lo almacena, este test se entera."""
        field_names = {field.name for field in PatientInvoice._meta.get_fields()}
        assert 'payment_state' not in field_names
        assert 'amount_collected' not in field_names


# ---------------------------------------------------------------------------
# Aislamiento y conservación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsolationAndSurvival:
    def test_a_payment_cannot_belong_to_another_clinic(
        self, issued_invoice_a, clinic_b
    ):
        with pytest.raises(ValidationError):
            Payment.objects.create(
                invoice=issued_invoice_a, clinic=clinic_b,
                amount=Decimal('10.00'), method=Payment.Method.CASH,
            )

        assert Payment.all_objects.count() == 0

    def test_the_receipt_survives_the_patient(self, payment_a, patient_a):
        number = payment_a.receipt_number
        patient_a.delete()

        stored = Payment.objects.get(receipt_number=number)
        assert stored.amount == Decimal('20.00')
        assert stored.frozen_patient_name == 'John Doe'
        assert stored.frozen_invoice_number.startswith('F-')


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestThePaymentIsAudited:
    def _logs_for(self, instance):
        from audit.models import ChangeLog

        return ChangeLog.objects.filter(
            model_label=instance._meta.label, object_id=str(instance.pk)
        ).order_by('timestamp')

    def test_registering_a_payment_is_recorded(self, payment_a, patient_a):
        from audit.models import ChangeLog

        log = self._logs_for(payment_a).filter(action=ChangeLog.Action.CREATE).get()
        assert log.patient_id == patient_a.pk

    def test_the_amount_and_method_are_recorded_in_the_clear(self, payment_a):
        from audit.models import ChangeLog

        log = self._logs_for(payment_a).filter(action=ChangeLog.Action.CREATE).get()
        assert log.changes['amount']['after'] == '20.00'
        assert log.changes['method']['after'] == 'card'


# ---------------------------------------------------------------------------
# Quién cobró
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWhoCollected:
    def test_the_author_is_optional(self, payment_a):
        assert payment_a.created_by_id is None
        assert payment_a.author is None
        assert payment_a.frozen_created_by_name == ''

    def test_the_author_name_is_frozen_on_creation(
        self, issued_invoice_a, professional_a
    ):
        payment = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('20.00'),
            method=Payment.Method.CARD, created_by=professional_a,
        )

        assert payment.frozen_created_by_name == str(professional_a)

    def test_the_author_cannot_be_changed_afterwards(
        self, payment_a, professional_a
    ):
        """El recibo nace confirmado: tampoco cambia de manos."""
        payment_a.created_by = professional_a

        with pytest.raises(PaymentFrozen):
            payment_a.save()

    def test_the_author_name_survives_the_professional(
        self, issued_invoice_a, staff_user
    ):
        """El recibo no añade un bloqueo al borrado, y no pierde el nombre.

        Se da de baja al profesional del `staff_user`: el de `professional_a`
        está protegido por sus visitas y no se puede borrar.
        """
        author = staff_user.professional_profile
        name = str(author)
        payment = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('20.00'),
            method=Payment.Method.CASH, created_by=author,
        )

        author.delete()

        stored = Payment.objects.get(pk=payment.pk)
        assert stored.author is None
        assert stored.frozen_created_by_name == name

    def test_a_professional_from_another_clinic_is_rejected(
        self, issued_invoice_a, admin_user_b
    ):
        foreign = admin_user_b.professional_profile

        with pytest.raises(ValidationError):
            Payment.objects.create(
                invoice=issued_invoice_a, amount=Decimal('10.00'),
                method=Payment.Method.CASH, created_by=foreign,
            )

        assert Payment.all_objects.count() == 0

    def test_the_author_is_recorded_in_the_change_log(
        self, issued_invoice_a, professional_a
    ):
        from audit.models import ChangeLog

        payment = Payment.objects.create(
            invoice=issued_invoice_a, amount=Decimal('20.00'),
            method=Payment.Method.CARD, created_by=professional_a,
        )

        log = ChangeLog.objects.filter(
            model_label=payment._meta.label, object_id=str(payment.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert log.changes['created_by']['after'] == professional_a.pk
