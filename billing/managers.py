"""Managers y utilidades de facturación.

- `next_invoice_number()` / `next_receipt_number()`: correlativos por clínica y
  año, con bloqueo de fila. Es el mismo patrón que
  `clinical.managers.next_history_number`, y por el mismo motivo: dos emisiones
  simultáneas no pueden obtener el mismo número. Son **dos series
  independientes**, cada una con su contador: una factura y un recibo son dos
  documentos distintos, y compartir tabla haría además que emitir una factura
  bloqueara el registro de un cobro.
- `PatientInvoiceQuerySet`: los tres estados de una factura, resueltos en un
  sitio en vez de repartidos por las vistas, y el estado de cobro anotado en una
  sola consulta (`with_collection()`).
- `PaymentQuerySet`: los cobros vivos de una factura.
- `unbilled_procedures()`: lo que le queda por cobrar a un paciente. Es la
  consulta con la que se construye una factura, así que vive aquí.
"""
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce

from core.managers import SoftDeleteQuerySet


class PatientInvoiceQuerySet(SoftDeleteQuerySet):
    """Consultas de facturas de paciente."""

    def drafts(self):
        return self.filter(status=self.model.Status.DRAFT)

    def issued(self):
        return self.filter(status=self.model.Status.ISSUED)

    def voided(self):
        return self.filter(status=self.model.Status.VOID)

    def for_patient(self, patient):
        return self.filter(patient=patient)

    def with_collection(self):
        """Anota `amount_collected` y deriva `payment_state`, en una sola consulta.

        El estado de cobro **no se almacena**: es una función de los pagos, y un
        campo lo obligaría a mantenerse sincronizado a mano en cada alta, cada
        anulación y cada reembolso futuro. Un campo desincronizado con el dinero
        real es peor que no tener campo.

        Dos detalles que no son opcionales:

        - El `filter=Q(payments__deleted_at__isnull=True)` va dentro del `Sum`.
          Una agregación sobre una relación inversa consulta la tabla entera y
          NO pasa por el manager por defecto del modelo relacionado, así que sin
          él un pago borrado lógicamente seguiría sumando.
        - Son dos `annotate()` encadenados y no uno: `payment_state` se apoya en
          el alias `amount_collected`, que no existe todavía dentro de la misma
          llamada.

        Cuidado al combinarlo con otra agregación sobre `procedures`: dos JOINs
        a dos tablas hijas multiplican las filas y ambas sumas salen infladas.
        Si hace falta, sepáralas en consultas distintas o usa subconsultas.
        """
        model = self.model
        return self.annotate(
            amount_collected=Coalesce(
                Sum('payments__amount', filter=Q(payments__deleted_at__isnull=True)),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ).annotate(
            payment_state=Case(
                When(
                    amount_collected__lte=Decimal('0.00'),
                    then=Value(model.PaymentState.UNPAID),
                ),
                When(
                    amount_collected__lt=F('total'),
                    then=Value(model.PaymentState.PARTIAL),
                ),
                default=Value(model.PaymentState.PAID),
                output_field=models.CharField(),
            ),
        )


class PaymentQuerySet(SoftDeleteQuerySet):
    """Consultas de cobros."""

    def for_invoice(self, invoice):
        return self.filter(invoice=invoice)

    def chronological(self):
        """Del cobro más antiguo al más reciente.

        El `ordering` del modelo es el contrario (lo reciente primero, que es
        como se lee una ficha); una secuencia de cobros parciales se lee al
        derecho y se pide explícitamente, como la evolución de una lesión.
        """
        return self.order_by('paid_at', 'id')


class PaymentManager(models.Manager.from_queryset(PaymentQuerySet)):
    """Manager por defecto de los cobros: oculta los borrados lógicamente."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class PatientInvoiceManager(models.Manager.from_queryset(PatientInvoiceQuerySet)):
    """Manager por defecto de las facturas: oculta las borradas lógicamente."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


def unbilled_procedures(patient):
    """Procedimientos de `patient` que aún no cuelgan de ninguna factura.

    En orden cronológico, que es como se leen las líneas de una factura. Los
    procedimientos borrados lógicamente quedan fuera: no se cobra lo que se dio
    de baja.
    """
    from clinical.models import PerformedProcedure

    return PerformedProcedure.objects.filter(
        visit__episode__history__patient=patient, invoice__isnull=True,
    ).order_by('performed_at', 'id')


def next_invoice_number(clinic) -> str:
    """Siguiente número de factura para `clinic`: ``F-{año}-{correlativo}``.

    El correlativo se reinicia por año y es único dentro de la clínica —cada
    clínica lleva su propia serie, que es justo lo que exige una numeración
    correlativa sin saltos—. Se apoya en un contador con bloqueo de fila
    (`select_for_update`) para que dos emisiones concurrentes no obtengan el
    mismo número; el `unique_together=(clinic, number)` de la factura es la red
    de seguridad.
    """
    from django.utils import timezone

    from billing.models import InvoiceSequence

    year = timezone.now().year
    with transaction.atomic():
        sequence, _ = (
            InvoiceSequence.objects.select_for_update()
            .get_or_create(clinic=clinic, year=year)
        )
        sequence.last_value = models.F('last_value') + 1
        sequence.save(update_fields=['last_value'])
        sequence.refresh_from_db(fields=['last_value'])
    return f'F-{year}-{sequence.last_value:05d}'


def next_receipt_number(clinic) -> str:
    """Siguiente número de recibo para `clinic`: ``R-{año}-{correlativo}``.

    Serie propia, separada de la de facturas: un recibo prueba que entró el
    dinero y una factura prueba qué se debía, y numerarlos juntos haría ilegibles
    los dos. Por lo demás, mismo mecanismo que `next_invoice_number` —contador
    con bloqueo de fila y `unique_together=(clinic, receipt_number)` como red de
    seguridad—, y por el mismo motivo: dos cobros simultáneos no pueden llevarse
    el mismo número.
    """
    from django.utils import timezone

    from billing.models import ReceiptSequence

    year = timezone.now().year
    with transaction.atomic():
        sequence, _ = (
            ReceiptSequence.objects.select_for_update()
            .get_or_create(clinic=clinic, year=year)
        )
        sequence.last_value = models.F('last_value') + 1
        sequence.save(update_fields=['last_value'])
        sequence.refresh_from_db(fields=['last_value'])
    return f'R-{year}-{sequence.last_value:05d}'
