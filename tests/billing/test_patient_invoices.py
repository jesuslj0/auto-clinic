"""Factura de paciente: mientras es borrador es una vista; al emitirla es un documento.

Lo que se defiende aquí es una sola idea con varias caras, la misma que sostiene
el resto del proyecto un nivel más abajo: **lo que se entregó al paciente no se
reescribe**. Un borrador se toca todo lo que haga falta y el importe se
recalcula; una factura emitida copia sus líneas, congela su total, gasta un
número de la serie de su clínica y deja de mirar los procedimientos.

De ahí se sigue todo lo demás: dar de baja un procedimiento después no cambia lo
que dice la factura, corregir una emitida es anularla y emitir otra, y una
anulada conserva su número y su detalle porque los tiene copiados, no
referenciados.
"""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from billing.exceptions import (
    EmptyInvoice,
    InvoiceFrozen,
    InvoiceNotDraft,
    InvoiceNotIssued,
)
from billing.managers import unbilled_procedures
from billing.models import InvoiceSequence, PatientInvoice
from clinical.exceptions import ProtectedClinicalRecord
from clinical.models import PerformedProcedure
from core.managers import ProtectedRecordError


@pytest.fixture
def visit_b(db, patient_b, admin_user_b):
    """Visita de patient_b, para probar que las dos clínicas no se rozan."""
    from clinical.models import Episode, Visit

    episode = Episode.objects.create(
        history=patient_b.medical_history, reason='Revisión',
    )
    return Visit.objects.create(
        episode=episode, professional=admin_user_b.professional_profile,
    )


@pytest.fixture
def second_procedure_a(db, visit_a, service_a):
    """Un segundo procedimiento del mismo paciente, de 30.00."""
    return PerformedProcedure.objects.create(
        visit=visit_a, service=service_a, frozen_price=Decimal('30.00'),
    )


# ---------------------------------------------------------------------------
# Borrador
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestADraftIsALiveView:
    def test_a_new_invoice_is_an_empty_draft(self, draft_invoice_a):
        assert draft_invoice_a.is_draft
        assert draft_invoice_a.number is None
        assert draft_invoice_a.issued_at is None
        assert draft_invoice_a.total == Decimal('0.00')
        assert draft_invoice_a.lines == []

    def test_adding_a_procedure_updates_the_total(self, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        draft_invoice_a.refresh_from_db()
        procedure_a.refresh_from_db()
        assert draft_invoice_a.total == Decimal('50.00')
        assert procedure_a.invoice_id == draft_invoice_a.pk

    def test_adding_two_procedures_adds_up(
        self, draft_invoice_a, procedure_a, second_procedure_a
    ):
        draft_invoice_a.add_procedure(procedure_a)
        draft_invoice_a.add_procedure(second_procedure_a)

        assert draft_invoice_a.total == Decimal('80.00')

    def test_removing_a_procedure_updates_the_total(
        self, draft_invoice_a, procedure_a, second_procedure_a
    ):
        draft_invoice_a.add_procedure(procedure_a)
        draft_invoice_a.add_procedure(second_procedure_a)

        draft_invoice_a.remove_procedure(second_procedure_a)

        draft_invoice_a.refresh_from_db()
        second_procedure_a.refresh_from_db()
        assert draft_invoice_a.total == Decimal('50.00')
        assert second_procedure_a.invoice_id is None

    def test_adding_the_same_procedure_twice_is_a_no_op(
        self, draft_invoice_a, procedure_a
    ):
        draft_invoice_a.add_procedure(procedure_a)
        draft_invoice_a.add_procedure(procedure_a)

        assert draft_invoice_a.total == Decimal('50.00')
        assert draft_invoice_a.procedures.count() == 1

    def test_a_procedure_cannot_be_in_two_invoices(
        self, draft_invoice_a, procedure_a, clinic_a, patient_a
    ):
        draft_invoice_a.add_procedure(procedure_a)
        other = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)

        with pytest.raises(ValidationError):
            other.add_procedure(procedure_a)

        procedure_a.refresh_from_db()
        assert procedure_a.invoice_id == draft_invoice_a.pk

    def test_removing_a_procedure_that_is_not_there_is_rejected(
        self, draft_invoice_a, procedure_a
    ):
        with pytest.raises(ValidationError):
            draft_invoice_a.remove_procedure(procedure_a)

    def test_unbilled_procedures_lists_what_is_left_to_charge(
        self, patient_a, draft_invoice_a, procedure_a, second_procedure_a
    ):
        assert set(unbilled_procedures(patient_a)) == {procedure_a, second_procedure_a}

        draft_invoice_a.add_procedure(procedure_a)

        assert list(unbilled_procedures(patient_a)) == [second_procedure_a]

    def test_a_soft_deleted_procedure_is_not_pending_to_charge(
        self, patient_a, procedure_a
    ):
        procedure_a.delete()

        assert list(unbilled_procedures(patient_a)) == []


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIssuingClosesTheDocument:
    def test_issuing_takes_a_number_and_a_date(self, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        draft_invoice_a.issue()

        assert draft_invoice_a.is_issued
        assert draft_invoice_a.number is not None
        assert draft_invoice_a.issued_at is not None

    def test_issuing_freezes_the_total(self, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        draft_invoice_a.issue()

        assert draft_invoice_a.total == Decimal('50.00')
        assert PatientInvoice.objects.get(pk=draft_invoice_a.pk).total == Decimal('50.00')

    def test_issuing_copies_the_lines_literally(self, issued_invoice_a, procedure_a):
        """Las líneas son una copia, no una consulta: se leen sin resolver FKs."""
        stored = PatientInvoice.objects.get(pk=issued_invoice_a.pk)

        assert len(stored.lines) == 1
        line = stored.lines[0]
        assert line['service_name'] == 'Consultation'
        assert line['price'] == '50.00'
        assert line['procedure_id'] == procedure_a.pk

    def test_issuing_freezes_the_patient_name(self, issued_invoice_a, patient_a):
        assert issued_invoice_a.frozen_patient_name == str(patient_a)

    def test_the_lines_keep_the_order_in_which_things_were_done(
        self, draft_invoice_a, procedure_a, second_procedure_a
    ):
        draft_invoice_a.add_procedure(second_procedure_a)
        draft_invoice_a.add_procedure(procedure_a)

        draft_invoice_a.issue()

        ids = [line['procedure_id'] for line in draft_invoice_a.lines]
        assert ids == sorted(
            [procedure_a.pk, second_procedure_a.pk],
            key=lambda pk: PerformedProcedure.objects.get(pk=pk).performed_at,
        )

    def test_an_empty_invoice_cannot_be_issued(self, draft_invoice_a):
        with pytest.raises(EmptyInvoice):
            draft_invoice_a.issue()

        draft_invoice_a.refresh_from_db()
        assert draft_invoice_a.is_draft
        assert draft_invoice_a.number is None

    def test_an_issued_invoice_cannot_be_issued_again(self, issued_invoice_a):
        number = issued_invoice_a.number

        with pytest.raises(InvoiceNotDraft):
            issued_invoice_a.issue()

        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.number == number


# ---------------------------------------------------------------------------
# Lo de después no alcanza al documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNothingReachesBackwardsIntoAnIssuedInvoice:
    def test_raising_the_catalog_price_does_not_move_the_invoice(
        self, issued_invoice_a, service_a
    ):
        """El caso de siempre: en enero suben los precios."""
        service_a.price = Decimal('65.00')
        service_a.save()

        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.total == Decimal('50.00')
        assert issued_invoice_a.lines[0]['price'] == '50.00'

    def test_soft_deleting_a_charged_procedure_does_not_move_the_invoice(
        self, issued_invoice_a, procedure_a
    ):
        """Dar de baja un procedimiento no reescribe una factura ya entregada."""
        procedure_a.delete()

        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.total == Decimal('50.00')
        assert len(issued_invoice_a.lines) == 1

    def test_the_total_cannot_be_edited_by_hand(self, issued_invoice_a):
        issued_invoice_a.total = Decimal('10.00')

        with pytest.raises(InvoiceFrozen):
            issued_invoice_a.save()

        assert PatientInvoice.objects.get(pk=issued_invoice_a.pk).total == Decimal('50.00')

    def test_the_number_cannot_be_edited_by_hand(self, issued_invoice_a):
        issued_invoice_a.number = 'F-1999-00001'

        with pytest.raises(InvoiceFrozen):
            issued_invoice_a.save()

    def test_the_lines_cannot_be_edited_by_hand(self, issued_invoice_a):
        issued_invoice_a.lines[0]['price'] = '5.00'

        with pytest.raises(InvoiceFrozen):
            issued_invoice_a.save()

    def test_the_patient_cannot_be_swapped(self, issued_invoice_a, patient_b):
        issued_invoice_a.patient = patient_b

        with pytest.raises(InvoiceFrozen):
            issued_invoice_a.save()

    def test_no_procedure_can_be_added_to_an_issued_invoice(
        self, issued_invoice_a, second_procedure_a
    ):
        with pytest.raises(InvoiceNotDraft):
            issued_invoice_a.add_procedure(second_procedure_a)

    def test_no_procedure_can_be_removed_from_an_issued_invoice(
        self, issued_invoice_a, procedure_a
    ):
        with pytest.raises(InvoiceNotDraft):
            issued_invoice_a.remove_procedure(procedure_a)

    def test_a_charged_procedure_cannot_be_moved_to_another_invoice(
        self, issued_invoice_a, procedure_a, clinic_a, patient_a
    ):
        """La barrera no está solo en la puerta buena: tampoco por la FK."""
        other = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        procedure_a.refresh_from_db()
        procedure_a.invoice = other

        with pytest.raises(ProtectedClinicalRecord):
            procedure_a.save()

        procedure_a.refresh_from_db()
        assert procedure_a.invoice_id == issued_invoice_a.pk

    def test_a_charged_procedure_cannot_be_detached_by_hand(
        self, issued_invoice_a, procedure_a
    ):
        procedure_a.refresh_from_db()
        procedure_a.invoice = None

        with pytest.raises(ProtectedClinicalRecord):
            procedure_a.save()

    def test_an_issued_invoice_still_accepts_unrelated_saves(self, issued_invoice_a):
        """Congelado es el documento, no la fila: anular tiene que poder escribir."""
        issued_invoice_a.void_reason = 'Error en el importe'
        issued_invoice_a.save()

        assert PatientInvoice.objects.get(pk=issued_invoice_a.pk).void_reason


# ---------------------------------------------------------------------------
# La serie, por clínica
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEachClinicKeepsItsOwnSeries:
    def _issue_for(self, clinic, patient, visit, service):
        invoice = PatientInvoice.objects.create(clinic=clinic, patient=patient)
        invoice.add_procedure(
            PerformedProcedure.objects.create(visit=visit, service=service)
        )
        return invoice.issue()

    def test_numbers_are_correlative_within_a_clinic(
        self, clinic_a, patient_a, visit_a, service_a
    ):
        first = self._issue_for(clinic_a, patient_a, visit_a, service_a)
        second = self._issue_for(clinic_a, patient_a, visit_a, service_a)

        year = first.issued_at.year
        assert first.number == f'F-{year}-00001'
        assert second.number == f'F-{year}-00002'

    def test_two_clinics_can_hold_the_same_number(
        self, clinic_a, patient_a, visit_a, service_a,
        clinic_b, patient_b, visit_b, service_b,
    ):
        """Cada clínica lleva su propia serie: no comparten contador."""
        invoice_a = self._issue_for(clinic_a, patient_a, visit_a, service_a)
        invoice_b = self._issue_for(clinic_b, patient_b, visit_b, service_b)

        assert invoice_a.number == invoice_b.number
        assert PatientInvoice.objects.filter(number=invoice_a.number).count() == 2

    def test_the_counter_lives_per_clinic_and_year(
        self, clinic_a, patient_a, visit_a, service_a
    ):
        invoice = self._issue_for(clinic_a, patient_a, visit_a, service_a)

        sequence = InvoiceSequence.objects.get(
            clinic=clinic_a, year=invoice.issued_at.year
        )
        assert sequence.last_value == 1

    def test_the_same_number_cannot_repeat_within_a_clinic(
        self, clinic_a, patient_a, issued_invoice_a
    ):
        """Red de seguridad en base de datos, por debajo del contador."""
        with pytest.raises(IntegrityError), transaction.atomic():
            PatientInvoice.all_objects.create(
                clinic=clinic_a, patient=patient_a,
                number=issued_invoice_a.number,
                issued_at=issued_invoice_a.issued_at,
                status=PatientInvoice.Status.ISSUED,
            )

    def test_a_voided_number_is_never_reused(
        self, clinic_a, patient_a, visit_a, service_a
    ):
        """Una serie correlativa no tiene huecos ni repeticiones."""
        first = self._issue_for(clinic_a, patient_a, visit_a, service_a)
        first.void('Error en el importe')

        second = self._issue_for(clinic_a, patient_a, visit_a, service_a)

        assert second.number != first.number


# ---------------------------------------------------------------------------
# Anulación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVoidingInsteadOfCorrecting:
    def test_voiding_records_when_and_why(self, issued_invoice_a):
        issued_invoice_a.void('Importe equivocado')

        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.is_void
        assert issued_invoice_a.voided_at is not None
        assert issued_invoice_a.void_reason == 'Importe equivocado'

    def test_a_voided_invoice_is_still_readable_whole(self, issued_invoice_a):
        """Lo anulado no se vacía: sigue diciendo qué se cobró y por cuánto."""
        number = issued_invoice_a.number
        issued_invoice_a.void('Error')

        stored = PatientInvoice.objects.get(pk=issued_invoice_a.pk)
        assert stored.number == number
        assert stored.total == Decimal('50.00')
        assert stored.lines[0]['service_name'] == 'Consultation'

    def test_voiding_frees_the_procedures(
        self, issued_invoice_a, procedure_a, patient_a
    ):
        issued_invoice_a.void('Error')

        procedure_a.refresh_from_db()
        assert procedure_a.invoice_id is None
        assert list(unbilled_procedures(patient_a)) == [procedure_a]

    def test_a_freed_procedure_can_be_charged_again(
        self, issued_invoice_a, procedure_a, clinic_a, patient_a
    ):
        issued_invoice_a.void('Error')

        corrected = PatientInvoice.objects.create(clinic=clinic_a, patient=patient_a)
        procedure_a.refresh_from_db()
        corrected.add_procedure(procedure_a)
        corrected.issue()

        assert corrected.total == Decimal('50.00')
        assert corrected.number != issued_invoice_a.number

    def test_a_draft_cannot_be_voided(self, draft_invoice_a):
        with pytest.raises(InvoiceNotIssued):
            draft_invoice_a.void()

    def test_a_voided_invoice_cannot_be_voided_again(self, issued_invoice_a):
        issued_invoice_a.void('Error')

        with pytest.raises(InvoiceNotIssued):
            issued_invoice_a.void('Otra vez')

    def test_a_voided_invoice_accepts_no_new_procedures(
        self, issued_invoice_a, second_procedure_a
    ):
        issued_invoice_a.void('Error')

        with pytest.raises(InvoiceNotDraft):
            issued_invoice_a.add_procedure(second_procedure_a)


# ---------------------------------------------------------------------------
# Borrado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnlyDraftsAreThrownAway:
    def test_a_draft_is_soft_deleted_and_frees_its_procedures(
        self, draft_invoice_a, procedure_a
    ):
        draft_invoice_a.add_procedure(procedure_a)

        draft_invoice_a.delete()

        procedure_a.refresh_from_db()
        assert procedure_a.invoice_id is None
        assert not PatientInvoice.objects.filter(pk=draft_invoice_a.pk).exists()
        assert PatientInvoice.all_objects.get(pk=draft_invoice_a.pk).is_deleted

    def test_an_issued_invoice_cannot_be_deleted(self, issued_invoice_a):
        with pytest.raises(ProtectedRecordError):
            issued_invoice_a.delete()

        assert PatientInvoice.objects.filter(pk=issued_invoice_a.pk).exists()

    def test_a_voided_invoice_cannot_be_deleted_either(self, issued_invoice_a):
        issued_invoice_a.void('Error')

        with pytest.raises(ProtectedRecordError):
            issued_invoice_a.delete()

    def test_a_queryset_delete_respects_the_veto(self, issued_invoice_a):
        """El `delete()` que se olvida es el del queryset."""
        with pytest.raises(ProtectedRecordError):
            PatientInvoice.objects.filter(pk=issued_invoice_a.pk).delete()

        assert PatientInvoice.objects.filter(pk=issued_invoice_a.pk).exists()


# ---------------------------------------------------------------------------
# Multi-tenancy y conservación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsolationAndSurvival:
    def test_an_invoice_cannot_be_issued_to_another_clinics_patient(
        self, clinic_a, patient_b
    ):
        with pytest.raises(ValidationError):
            PatientInvoice.objects.create(clinic=clinic_a, patient=patient_b)

        assert PatientInvoice.all_objects.count() == 0

    def test_a_procedure_of_another_patient_never_enters(
        self, draft_invoice_a, visit_b, service_b
    ):
        foreign = PerformedProcedure.objects.create(visit=visit_b, service=service_b)

        with pytest.raises(ValidationError):
            draft_invoice_a.add_procedure(foreign)

        foreign.refresh_from_db()
        assert foreign.invoice_id is None

    def test_the_invoice_survives_the_patient(self, issued_invoice_a, patient_a):
        """La conservación de una factura es fiscal: no depende del paciente."""
        number = issued_invoice_a.number
        patient_a.delete()

        stored = PatientInvoice.objects.get(number=number)
        assert stored.total == Decimal('50.00')
        assert stored.frozen_patient_name == 'John Doe'


# ---------------------------------------------------------------------------
# Barreras de base de datos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheDatabaseHoldsTheLine:
    def test_a_negative_total_is_rejected(self, clinic_a, patient_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            PatientInvoice.all_objects.create(
                clinic=clinic_a, patient=patient_a, total=Decimal('-1.00'),
            )

    def test_a_draft_with_a_number_is_rejected(self, clinic_a, patient_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            PatientInvoice.all_objects.create(
                clinic=clinic_a, patient=patient_a, number='F-2026-00001',
            )

    def test_an_issued_invoice_without_a_number_is_rejected(self, clinic_a, patient_a):
        from django.utils import timezone

        with pytest.raises(IntegrityError), transaction.atomic():
            PatientInvoice.all_objects.create(
                clinic=clinic_a, patient=patient_a,
                status=PatientInvoice.Status.ISSUED, issued_at=timezone.now(),
            )


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheInvoiceIsAudited:
    def _logs_for(self, instance):
        from audit.models import ChangeLog

        return ChangeLog.objects.filter(
            model_label=instance._meta.label, object_id=str(instance.pk)
        ).order_by('timestamp')

    def test_creating_an_invoice_is_recorded(self, draft_invoice_a, patient_a):
        from audit.models import ChangeLog

        log = self._logs_for(draft_invoice_a).filter(
            action=ChangeLog.Action.CREATE
        ).get()
        assert log.patient_id == patient_a.pk

    def test_issuing_is_recorded(self, issued_invoice_a):
        from audit.models import ChangeLog

        log = self._logs_for(issued_invoice_a).filter(
            action=ChangeLog.Action.UPDATE
        ).last()
        assert log.changes['status'] == {'before': 'draft', 'after': 'issued'}
        assert log.changes['number']['after'] == issued_invoice_a.number

    def test_the_amount_is_recorded_in_the_clear(self, issued_invoice_a):
        """El importe NO va enmascarado, y a propósito: es justo lo que hay que
        poder reconstruir si mañana se discute una factura."""
        totals = [
            log.changes['total'] for log in self._logs_for(issued_invoice_a)
            if 'total' in log.changes
        ]
        assert {'before': '0.00', 'after': '50.00'} in totals

    def test_the_void_reason_never_leaks_to_the_change_log(self, issued_invoice_a):
        import json

        issued_invoice_a.void('El paciente no era diabético, se cobró de más')

        blob = json.dumps(
            [log.changes for log in self._logs_for(issued_invoice_a)],
            ensure_ascii=False,
        )
        assert 'diabético' not in blob
        assert self._logs_for(issued_invoice_a).last().changes['void_reason'] == {
            'changed': True
        }


# ---------------------------------------------------------------------------
# Quién la registró
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWhoRegisteredTheInvoice:
    def test_the_author_is_optional(self, draft_invoice_a):
        """No todo canal de entrada tiene un profesional detrás."""
        assert draft_invoice_a.created_by_id is None
        assert draft_invoice_a.author is None
        assert draft_invoice_a.frozen_created_by_name == ''

    def test_the_author_name_is_frozen_on_creation(
        self, clinic_a, patient_a, professional_a
    ):
        invoice = PatientInvoice.objects.create(
            clinic=clinic_a, patient=patient_a, created_by=professional_a,
        )

        assert invoice.frozen_created_by_name == str(professional_a)

    def test_a_draft_can_still_change_hands(
        self, draft_invoice_a, professional_a
    ):
        """Mientras es borrador, quién la registra todavía se corrige."""
        draft_invoice_a.created_by = professional_a
        draft_invoice_a.save()

        stored = PatientInvoice.objects.get(pk=draft_invoice_a.pk)
        assert stored.created_by_id == professional_a.pk

    def test_issuing_freezes_the_author(
        self, clinic_a, patient_a, professional_a, procedure_a
    ):
        invoice = PatientInvoice.objects.create(
            clinic=clinic_a, patient=patient_a, created_by=professional_a,
        )
        invoice.add_procedure(procedure_a)
        invoice.issue()

        invoice.created_by = None
        with pytest.raises(InvoiceFrozen):
            invoice.save()

    def test_the_author_name_survives_the_professional(
        self, clinic_a, patient_a, procedure_a, staff_user
    ):
        """Un documento fiscal no pierde quién lo hizo porque alguien cause baja.

        El profesional que se da de baja es el del `staff_user` y no el de la
        fixture `professional_a`: ese está protegido por sus visitas
        (`Visit.professional` es `PROTECT`) y no se puede borrar. Lo que se
        prueba aquí es que la factura NO añade un bloqueo propio.
        """
        author = staff_user.professional_profile
        name = str(author)
        invoice = PatientInvoice.objects.create(
            clinic=clinic_a, patient=patient_a, created_by=author,
        )
        invoice.add_procedure(procedure_a)
        invoice.issue()

        author.delete()

        stored = PatientInvoice.objects.get(pk=invoice.pk)
        assert stored.author is None
        assert stored.frozen_created_by_name == name

    def test_a_professional_from_another_clinic_is_rejected(
        self, clinic_a, patient_a, admin_user_b
    ):
        foreign = admin_user_b.professional_profile

        with pytest.raises(ValidationError):
            PatientInvoice.objects.create(
                clinic=clinic_a, patient=patient_a, created_by=foreign,
            )
