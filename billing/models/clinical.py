"""Factura de paciente: lo que se cobró, congelado el día que se emitió.

Una factura no es una vista sobre los procedimientos: es un **documento**. El
mismo argumento que sostiene el resto del proyecto —la anamnesis guarda un
snapshot literal, el consentimiento copia el texto firmado, el procedimiento
congela el precio del catálogo— se aplica aquí un nivel más arriba.

Mientras es borrador, la factura sí es una vista: se le añaden y quitan
procedimientos y el importe se recalcula. Al emitirla se cierra: copia sus
líneas literalmente en `lines`, fija el total, toma número de la serie de la
clínica y no vuelve a mirar los procedimientos nunca más. Dar de baja después
un procedimiento, o corregir el catálogo, no puede reescribir hacia atrás lo que
dice una factura ya entregada al paciente.

Por eso la FK `PerformedProcedure.invoice` es, tras la emisión, **procedencia y
estado** («esto ya se cobró»), igual que `PerformedProcedure.service` es
procedencia y no fuente de verdad del importe. Lo que la factura dice está en
`lines` y en `total`.

Y por eso una factura emitida no se corrige: se anula (`void()`) y se emite otra.
Anularla libera sus procedimientos para que puedan volver a facturarse, y la
anulada sigue siendo legible entera —con su número, su importe y su detalle—
porque su contenido está copiado, no referenciado.
"""
import copy
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from billing.exceptions import (
    EmptyInvoice,
    InvoiceFrozen,
    InvoiceHasPayments,
    InvoiceNotDraft,
    InvoiceNotIssued,
    InvoiceNotPayable,
    Overpayment,
    PaymentFrozen,
)
from billing.managers import (
    PatientInvoiceManager,
    PaymentManager,
    next_invoice_number,
    next_receipt_number,
)
from core.managers import AllObjectsManager, ProtectedRecordError
from core.models import SoftDeleteModel, TimeStampedModel


def _frozen_state(instance, fields, loaded_names=None) -> dict:
    """Copia profunda del valor de `fields` en `instance`.

    Gemelo del helper homónimo de `clinical.models`, y deliberadamente local:
    `billing` no importa nada privado de la capa clínica. La copia es profunda
    porque `lines` es un `JSONField` y se muta in situ; sin ella, el «valor
    cargado» y el actual serían el mismo objeto y ningún cambio se vería.
    """
    if loaded_names is not None:
        fields = [name for name in fields if name in loaded_names]
    return {name: copy.deepcopy(getattr(instance, name)) for name in fields}


def _safe_author(instance):
    """El profesional que registró el documento, o `None` si ya no existe.

    El FK es `DO_NOTHING` + `db_constraint=False`, así que puede quedar
    apuntando a un id difunto; leerlo a pelo lanzaría `DoesNotExist`. Para saber
    QUIÉN lo registró está el nombre congelado al lado, que es lo que se enseña.
    """
    if instance.created_by_id is None:
        return None
    try:
        return instance.created_by
    except ObjectDoesNotExist:
        return None


def _changed_frozen_fields(instance, loaded: dict) -> list[str]:
    """Campos congelados que difieren de lo que se cargó de la base de datos."""
    return sorted(
        name for name, value in loaded.items() if getattr(instance, name) != value
    )


class InvoiceSequence(models.Model):
    """Contador correlativo de facturas por clínica y año.

    Infraestructura interna: NO es dato de paciente y NO se audita. Existe solo
    para generar `PatientInvoice.number` de forma segura frente a emisiones
    concurrentes (ver `billing.managers.next_invoice_number`).
    """

    clinic = models.ForeignKey('core.Clinic', on_delete=models.CASCADE, related_name='+')
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'billing_invoice_sequence'
        unique_together = ('clinic', 'year')
        verbose_name = 'serie de facturación'
        verbose_name_plural = 'series de facturación'

    def __str__(self):
        return f'{self.clinic_id} {self.year}: {self.last_value}'


class PatientInvoice(SoftDeleteModel, TimeStampedModel):
    """Agrupa procedimientos para cobrar. Total y líneas congelados al emitir.

    Los FK a `Patient` y `Clinic` son `DO_NOTHING` + `db_constraint=False`, el
    mismo patrón que `MedicalHistory`: borrar un paciente ni arrastra ni bloquea
    sus facturas. Una factura tiene que sobrevivir al paciente —la obligación de
    conservación es fiscal, no clínica— y por eso guarda además el nombre
    congelado: una factura huérfana sigue diciendo a quién se le emitió.
    """

    # El documento, una vez emitido. No se reescribe jamás.
    FROZEN_FIELDS = (
        'clinic_id', 'patient_id', 'frozen_patient_name',
        'created_by_id', 'frozen_created_by_name',
        'number', 'issued_at', 'total', 'lines',
    )

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        ISSUED = 'issued', 'Emitida'
        VOID = 'void', 'Anulada'

    class PaymentState(models.TextChoices):
        """Cuánto se ha cobrado de la factura. NO es un campo: se deriva.

        Lo anota `PatientInvoiceQuerySet.with_collection()` a partir de la suma
        de los pagos vivos. Vive aquí, y no en el queryset, para que las
        plantillas y las vistas tengan un solo vocabulario que nombrar.
        """

        UNPAID = 'unpaid', 'Impagada'
        PARTIAL = 'partial', 'Parcial'
        PAID = 'paid', 'Pagada'

    clinic = models.ForeignKey(
        'core.Clinic', on_delete=models.DO_NOTHING,
        related_name='patient_invoices', db_constraint=False,
    )
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.DO_NOTHING,
        related_name='invoices', db_constraint=False,
    )
    frozen_patient_name = models.CharField(
        max_length=301, blank=True,
        help_text='Nombre del paciente al emitir. Copia, no referencia: '
                  'la factura sigue siendo legible si el paciente se borra.',
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    number = models.CharField(
        max_length=20, null=True, blank=True,
        help_text='Número de factura, p. ej. F-2026-00001. Nulo mientras es borrador.',
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Importe cobrado. Se recalcula mientras es borrador y se congela al emitir.',
    )
    lines = models.JSONField(
        default=list, blank=True,
        help_text='Copia literal de las líneas cobradas al emitir. '
                  'Lo que la factura dice está aquí, no en las FKs.',
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(
        blank=True, help_text='Motivo de la anulación.',
    )
    # Quién la registró. `DO_NOTHING` + `db_constraint=False` y no el `SET_NULL`
    # de la capa clínica, por dos motivos que aquí pesan más: un `SET_NULL`
    # emite un `UPDATE` masivo al borrar al profesional, y sobre un modelo
    # auditado ese UPDATE se salta las señales —el cambio quedaría sin rastro—;
    # y borraría de un documento fiscal quién lo hizo. Un `Professional` sí
    # desaparece de verdad: cuelga de `User` con `CASCADE` y no tiene borrado
    # lógico. Por eso, además del FK, se congela el nombre.
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.DO_NOTHING,
        null=True, blank=True, related_name='+', db_constraint=False,
        help_text='Profesional que registró la factura. Vacío si no lo hubo.',
    )
    frozen_created_by_name = models.CharField(
        max_length=255, blank=True,
        help_text='Nombre de quien la registró, al emitir. Copia, no referencia.',
    )

    objects = PatientInvoiceManager()
    all_objects = AllObjectsManager()

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'billing_patient_invoice'
        unique_together = ('clinic', 'number')
        ordering = ['-issued_at', '-created_at', '-id']
        verbose_name = 'factura de paciente'
        verbose_name_plural = 'facturas de paciente'
        indexes = [
            models.Index(fields=['patient', 'status'], name='idx_invoice_patient_status'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total__gte=0),
                name='billing_patient_invoice_total_positive',
            ),
            # Un borrador no tiene número ni fecha de emisión, y una factura
            # emitida (o anulada, que estuvo emitida) los tiene los dos. Segundo
            # nivel, como en el resto del proyecto: un documento fiscal a medio
            # emitir no es un matiz de formulario.
            models.CheckConstraint(
                condition=(
                    models.Q(status='draft', number__isnull=True, issued_at__isnull=True)
                    | models.Q(
                        status__in=['issued', 'void'],
                        number__isnull=False, issued_at__isnull=False,
                    )
                ),
                name='billing_patient_invoice_number_only_when_issued',
            ),
        ]

    def __str__(self):
        return self.number or f'Borrador #{self.pk}'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        instance._loaded_status = instance.status
        return instance

    # -- Estado ------------------------------------------------------------

    @property
    def author(self):
        """Quien la registró, o `None` si ese profesional ya no existe."""
        return _safe_author(self)

    def _freeze_author_name(self):
        """Copia el nombre de quien la registró. Mientras es borrador se refresca."""
        author = self.author
        if author is not None:
            self.frozen_created_by_name = str(author)

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def is_issued(self) -> bool:
        return self.status == self.Status.ISSUED

    @property
    def is_void(self) -> bool:
        return self.status == self.Status.VOID

    # -- Contenido ---------------------------------------------------------

    def _line_from(self, procedure) -> dict:
        """Copia literal de un procedimiento como línea de factura.

        Se guarda lo que hay que poder leer años después sin resolver una sola
        FK. El `procedure_id` va como procedencia, nunca como fuente del importe.
        """
        return {
            'procedure_id': procedure.pk,
            'service_name': procedure.frozen_service_name,
            'price': str(procedure.frozen_price),
            'performed_at': procedure.performed_at.isoformat(),
            'laterality': procedure.laterality,
            'affected_zone': procedure.affected_zone,
        }

    def compute_total(self) -> Decimal:
        """Suma de los procedimientos enganchados. Solo tiene sentido en borrador.

        Una factura emitida no recalcula: su importe es `total`, congelado.
        """
        total = sum(
            (p.frozen_price for p in self.procedures.all()), Decimal('0.00')
        )
        return Decimal(total).quantize(Decimal('0.01'))

    def refresh_total(self):
        """Recalcula el importe del borrador y lo guarda."""
        self._require_draft()
        self.total = self.compute_total()
        self.save(update_fields=['total', 'updated_at'])

    def _require_draft(self):
        if not self.is_draft:
            raise InvoiceNotDraft(
                f'La factura {self} está {self.get_status_display().lower()}: '
                f'su contenido no se puede modificar. Anúlala y emite otra.'
            )

    def add_procedure(self, procedure):
        """Engancha un procedimiento al borrador y recalcula el importe."""
        self._require_draft()
        if procedure.invoice_id == self.pk:
            return
        if procedure.invoice_id is not None:
            raise ValidationError({
                'procedure': 'El procedimiento ya está en otra factura.'
            })
        if procedure.patient.pk != self.patient_id:
            raise ValidationError({
                'procedure': 'El procedimiento es de otro paciente.'
            })
        procedure.invoice = self
        procedure.save(update_fields=['invoice', 'updated_at'])
        self.refresh_total()

    def remove_procedure(self, procedure):
        """Desengancha un procedimiento del borrador y recalcula el importe."""
        self._require_draft()
        if procedure.invoice_id != self.pk:
            raise ValidationError({
                'procedure': 'El procedimiento no está en esta factura.'
            })
        procedure.invoice = None
        procedure.save(update_fields=['invoice', 'updated_at'])
        self.refresh_total()

    # -- Ciclo de vida -----------------------------------------------------

    @transaction.atomic
    def issue(self):
        """Emite la factura: copia sus líneas, fija el importe y toma número.

        A partir de aquí el documento está cerrado. Los procedimientos siguen
        colgando de ella como procedencia, pero lo que la factura dice ya no sale
        de ellos: sale de `lines` y `total`.
        """
        self._require_draft()
        procedures = list(self.procedures.order_by('performed_at', 'id'))
        if not procedures:
            raise EmptyInvoice(
                'Una factura sin procedimientos no se puede emitir.'
            )

        self.lines = [self._line_from(p) for p in procedures]
        self.total = sum(
            (Decimal(line['price']) for line in self.lines), Decimal('0.00')
        ).quantize(Decimal('0.01'))
        self.frozen_patient_name = str(self.patient)
        self._freeze_author_name()
        self.number = next_invoice_number(self.clinic)
        self.issued_at = timezone.now()
        self.status = self.Status.ISSUED
        self.save()
        return self

    # `amount_collected` y `payment_state` son propiedades CON setter, y eso no
    # es un adorno: `with_collection()` anota dos valores con estos mismos
    # nombres, y Django los asigna con `setattr` al construir cada fila. Contra
    # una property de solo lectura eso revienta ("has no setter"); con nombres
    # distintos habría dos vocabularios para lo mismo y la plantilla tendría que
    # saber por qué consulta llegó su factura.
    #
    # Así hay un solo nombre: si la instancia viene anotada se devuelve el valor
    # que ya trajo la consulta; si no, se calcula al vuelo. Lo que NO se hace es
    # cachear el cálculo: una factura suelta consulta cada vez y por tanto nunca
    # miente después de registrar un cobro.

    @property
    def amount_collected(self) -> Decimal:
        """Suma de los cobros vivos de esta factura.

        En un listado usa `PatientInvoice.objects.with_collection()`, que lo
        resuelve para todas las facturas en una consulta en vez de una por fila.
        """
        if '_amount_collected' in self.__dict__:
            return self.__dict__['_amount_collected']
        total = self.payments.aggregate(total=models.Sum('amount'))['total']
        return (total or Decimal('0.00')).quantize(Decimal('0.01'))

    @amount_collected.setter
    def amount_collected(self, value):
        self.__dict__['_amount_collected'] = value

    @property
    def amount_due(self) -> Decimal:
        """Lo que queda por cobrar. Nunca negativo: el sobrepago está vetado."""
        return (self.total - self.amount_collected).quantize(Decimal('0.01'))

    @property
    def payment_state(self) -> str:
        """Estado de cobro. Derivado de los pagos, NUNCA almacenado.

        Un campo obligaría a mantenerlo sincronizado a mano en cada cobro y cada
        anulación, y un campo desincronizado con el dinero real es peor que no
        tener campo.
        """
        if '_payment_state' in self.__dict__:
            return self.__dict__['_payment_state']
        collected = self.amount_collected
        if collected <= 0:
            return self.PaymentState.UNPAID
        if collected < self.total:
            return self.PaymentState.PARTIAL
        return self.PaymentState.PAID

    @payment_state.setter
    def payment_state(self, value):
        self.__dict__['_payment_state'] = value

    @transaction.atomic
    def void(self, reason=''):
        """Anula una factura emitida y libera sus procedimientos.

        No borra nada ni corrige nada: el número se queda gastado y el documento
        sigue legible entero, que es lo que exige una serie correlativa. Los
        procedimientos vuelven a estar pendientes de facturar, y se desenganchan
        uno a uno —nunca con un `update()` masivo, que se saltaría la auditoría—.

        **Una factura con cobros no se anula.** Anularla dejaría recibos
        apuntando a un documento sin efecto y dinero sin destino contable, y hoy
        no hay reembolso que deshaga un pago. Cuando lo haya, este veto es el
        sitio donde se relaja.
        """
        if not self.is_issued:
            raise InvoiceNotIssued(
                f'La factura {self} no está emitida: no hay nada que anular.'
            )
        if self.payments.exists():
            raise InvoiceHasPayments(
                f'La factura {self} tiene cobros registrados y no se puede '
                f'anular. Mientras no exista el reembolso, lo cobrado no se '
                f'deshace.'
            )
        self.status = self.Status.VOID
        self.voided_at = timezone.now()
        self.void_reason = reason
        self.save()

        for procedure in self.procedures.all():
            procedure.invoice = None
            procedure.save(update_fields=['invoice', 'updated_at'])
        return self

    # -- Integridad --------------------------------------------------------

    def _validate_clinic(self):
        """El paciente tiene que ser de la clínica que emite.

        La FK no lleva restricción en base de datos (`db_constraint=False`), así
        que nada impediría facturarle a un paciente de otro inquilino.
        """
        if self.patient_id and self.clinic_id:
            patient = self.patient
            if patient is not None and patient.clinic_id != self.clinic_id:
                raise ValidationError({
                    'patient': 'El paciente pertenece a otra clínica.'
                })

        author = self.author
        if author is not None and self.clinic_id and author.clinic_id != self.clinic_id:
            raise ValidationError({
                'created_by': 'El profesional pertenece a otra clínica.'
            })

    def clean(self):
        super().clean()
        self._validate_clinic()

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._validate_clinic()
            if not self.frozen_patient_name and self.patient_id:
                self.frozen_patient_name = str(self.patient)
            self._freeze_author_name()
        elif getattr(self, '_loaded_status', self.Status.DRAFT) != self.Status.DRAFT:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise InvoiceFrozen(
                    f'La factura {self} ya está emitida: '
                    f'{", ".join(changed)} no se puede modificar. '
                    f'Para corregirla, anúlala y emite otra.'
                )

        super().save(*args, **kwargs)
        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)
        self._loaded_status = self.status

    # -- Borrado -----------------------------------------------------------

    def can_be_deleted(self) -> bool:
        # Un borrador es papel de trabajo y se tira. Una factura emitida gastó
        # un número de la serie: ni siquiera lógicamente desaparece.
        return self.is_draft

    def delete(self, using=None, keep_parents=False):
        """Borrado lógico del borrador, soltando sus procedimientos.

        Django no cascadea el borrado lógico: si el borrador se va, sus
        procedimientos tienen que quedar otra vez pendientes de facturar, o
        quedarían colgando de una factura que nadie ve.
        """
        if not self.can_be_deleted():
            raise ProtectedRecordError(
                f'La factura {self} está emitida y no se puede borrar. '
                f'Para dejarla sin efecto, anúlala.'
            )
        for procedure in self.procedures.all():
            procedure.invoice = None
            procedure.save(update_fields=['invoice', 'updated_at'])
        super().delete(using=using, keep_parents=keep_parents)


# ---------------------------------------------------------------------------
# Cobros
# ---------------------------------------------------------------------------
#
# Una factura dice qué se debe; un pago dice que entró el dinero. Son dos hechos
# distintos y por eso son dos documentos: una factura puede cobrarse en varias
# veces, y cada una de esas veces es un recibo con su número, su fecha y su
# método.
#
# El congelado que aquí se aplica es el de `PerformedProcedure`, no el de la
# factura. La factura tiene una etapa editable —el borrador— y su guardián solo
# se activa al emitirla; un pago nace confirmado, así que queda cerrado desde el
# primer `save()` y no hay ningún estado en el que admita cambios.


class ReceiptSequence(models.Model):
    """Contador correlativo de recibos por clínica y año.

    Infraestructura interna: NO es dato de paciente y NO se audita. Tabla propia
    y no una columna más en `InvoiceSequence` porque son dos series distintas, y
    compartir fila las haría compartir también el bloqueo: emitir una factura
    frenaría el registro de un cobro sin ninguna razón.
    """

    clinic = models.ForeignKey('core.Clinic', on_delete=models.CASCADE, related_name='+')
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'billing_receipt_sequence'
        unique_together = ('clinic', 'year')
        verbose_name = 'serie de recibos'
        verbose_name_plural = 'series de recibos'

    def __str__(self):
        return f'{self.clinic_id} {self.year}: {self.last_value}'


class Payment(SoftDeleteModel, TimeStampedModel):
    """Un cobro contra una factura emitida. Una fila por entrada de dinero.

    Nace confirmado y congelado: no hay borrador, no se corrige y no se borra.
    Igual que la factura, guarda copias de lo que necesita para leerse solo
    —nombre del paciente y número de factura— y apunta a lo demás con
    `DO_NOTHING` + `db_constraint=False`: un recibo es un documento fiscal y
    tiene que sobrevivir a los registros a los que señala.

    Todo el trabajo ocurre en el `save()` del alta, no en la vista ni en el
    formulario: validar que la factura esté emitida, comprobar que no se cobre
    de más, congelar la copia y tomar número de la serie. Así da igual por dónde
    entre el pago —panel, admin, shell, un comando—, que las reglas son las
    mismas.
    """

    # Todo el recibo. Fijo desde que se registra.
    FROZEN_FIELDS = (
        'clinic_id', 'invoice_id', 'frozen_patient_name', 'frozen_invoice_number',
        'created_by_id', 'frozen_created_by_name',
        'receipt_number', 'paid_at', 'amount', 'method',
    )

    class Method(models.TextChoices):
        CARD = 'card', 'Tarjeta'
        TRANSFER = 'transfer', 'Transferencia'
        BIZUM = 'bizum', 'Bizum'
        CASH = 'cash', 'Efectivo'

    clinic = models.ForeignKey(
        'core.Clinic', on_delete=models.DO_NOTHING,
        related_name='payments', db_constraint=False,
    )
    invoice = models.ForeignKey(
        'billing.PatientInvoice', on_delete=models.DO_NOTHING,
        related_name='payments', db_constraint=False,
    )
    frozen_patient_name = models.CharField(
        max_length=301, blank=True,
        help_text='Nombre del paciente al cobrar. Copia, no referencia.',
    )
    frozen_invoice_number = models.CharField(
        max_length=20, blank=True,
        help_text='Número de la factura cobrada. Copia: el recibo se lee solo.',
    )
    receipt_number = models.CharField(
        max_length=20, blank=True,
        help_text='Número de recibo, p. ej. R-2026-00001. Se asigna al registrarlo.',
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Importe cobrado. Un recibo de cero euros no prueba nada.',
    )
    method = models.CharField(max_length=10, choices=Method.choices, db_index=True)
    paid_at = models.DateTimeField(
        default=timezone.now, help_text='Cuándo entró el dinero.',
    )
    # Quién cobró. Mismo criterio que en la factura (ver allí): `DO_NOTHING` +
    # nombre congelado, porque un recibo no puede perder quién lo hizo por que
    # el profesional se dé de baja, y un `SET_NULL` sobre un modelo auditado
    # borraría el dato con un `UPDATE` masivo que no deja rastro.
    created_by = models.ForeignKey(
        'appointments.Professional', on_delete=models.DO_NOTHING,
        null=True, blank=True, related_name='+', db_constraint=False,
        help_text='Profesional que registró el cobro. Vacío si no lo hubo.',
    )
    frozen_created_by_name = models.CharField(
        max_length=255, blank=True,
        help_text='Nombre de quien cobró, al registrar. Copia, no referencia.',
    )

    objects = PaymentManager()
    all_objects = AllObjectsManager()

    class Meta(SoftDeleteModel.Meta):
        abstract = False
        db_table = 'billing_payment'
        unique_together = ('clinic', 'receipt_number')
        ordering = ['-paid_at', '-id']
        verbose_name = 'cobro'
        verbose_name_plural = 'cobros'
        indexes = [
            # Los cobros de una factura, en orden. El índice simple sobre
            # `invoice` no se declara: un ForeignKey de Django ya lo trae.
            models.Index(fields=['invoice', 'paid_at'], name='idx_payment_invoice_paid'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='billing_payment_amount_positive',
            ),
        ]

    def __str__(self):
        return self.receipt_number or f'Cobro #{self.pk}'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_frozen = _frozen_state(instance, cls.FROZEN_FIELDS, field_names)
        return instance

    @property
    def patient(self):
        """Paciente al que se le cobró, subiendo por la factura."""
        return self.invoice.patient

    @property
    def author(self):
        """Quien lo cobró, o `None` si ese profesional ya no existe."""
        return _safe_author(self)

    # -- Alta --------------------------------------------------------------

    def _prepare_receipt(self):
        """Valida el cobro contra la factura y congela la copia. Solo en el alta.

        El nombre lleva sufijo porque `_prepare` está tomado: es un método
        interno de `ModelBase` que Django invoca al construir la clase.

        Debe correr dentro de la transacción del `save()`: bloquea la fila de la
        factura, y ese bloqueo es lo único que impide el sobrepago concurrente
        (ver la nota de `save()`).
        """
        if self.invoice_id is None:
            raise ValidationError({'invoice': 'Un cobro necesita su factura.'})

        # `all_objects` a propósito: una factura borrada lógicamente no debería
        # existir —una emitida no se puede borrar—, pero si existiera, cobrarla
        # sin enterarse sería peor que fallar aquí.
        invoice = (
            PatientInvoice.all_objects
            .select_for_update()
            .filter(pk=self.invoice_id)
            .first()
        )
        if invoice is None:
            raise ValidationError({'invoice': 'La factura no existe.'})
        if invoice.status != PatientInvoice.Status.ISSUED:
            raise InvoiceNotPayable(
                f'La factura {invoice} está '
                f'{invoice.get_status_display().lower()}: contra ella no entra '
                f'dinero. Solo se cobra una factura emitida.'
            )
        if invoice.is_deleted:
            raise ValidationError({'invoice': 'La factura está dada de baja.'})

        if self.clinic_id is None:
            self.clinic_id = invoice.clinic_id
        elif self.clinic_id != invoice.clinic_id:
            raise ValidationError({
                'clinic': 'El cobro es de otra clínica que la factura.'
            })

        if self.amount is None:
            raise ValidationError({'amount': 'Un cobro necesita su importe.'})

        collected = invoice.amount_collected
        if collected + self.amount > invoice.total:
            raise Overpayment(
                f'La factura {invoice} debe {invoice.total - collected} € y se '
                f'intentan cobrar {self.amount} €. Un cobro no puede superar lo '
                f'que queda pendiente.'
            )

        author = self.author
        if author is not None and author.clinic_id != self.clinic_id:
            raise ValidationError({
                'created_by': 'El profesional pertenece a otra clínica.'
            })

        self.frozen_patient_name = invoice.frozen_patient_name
        self.frozen_invoice_number = invoice.number or ''
        if author is not None:
            self.frozen_created_by_name = str(author)
        if not self.receipt_number:
            self.receipt_number = next_receipt_number(invoice.clinic)

    def save(self, *args, **kwargs):
        """Alta atómica y congelada; después, solo lecturas.

        **Nivel único de defensa contra el sobrepago, a conciencia.** El resto
        del proyecto defiende sus invariantes dos veces (validador + restricción
        en base de datos), pero «la suma de los pagos no supera el total» es una
        agregación sobre varias filas y PostgreSQL no la puede expresar como
        `CHECK`: una restricción solo ve la fila que se escribe. Lo único que
        queda en pie es el `select_for_update()` sobre la factura de
        `_prepare_receipt`,
        que serializa los cobros concurrentes de una misma factura —sin él, dos
        cobros simultáneos leerían los dos el mismo «cobrado hasta ahora», los
        dos pasarían la comprobación y la factura acabaría sobrecobrada—.

        De ahí se siguen dos reglas para quien toque esto:

        1. El bloqueo y la inserción tienen que estar en la MISMA transacción.
           Sacar el `super().save()` fuera del `atomic` suelta la fila antes de
           escribir y reabre exactamente la carrera que se está cerrando.
        2. Ningún alta de cobro puede saltarse este `save()`. `bulk_create()`
           no lo ejecuta —y además se salta la auditoría—: nunca sobre este
           modelo.
        """
        if self._state.adding:
            with transaction.atomic():
                self._prepare_receipt()
                super().save(*args, **kwargs)
        else:
            changed = _changed_frozen_fields(self, getattr(self, '_loaded_frozen', {}))
            if changed:
                raise PaymentFrozen(
                    f'El cobro {self} está congelado: '
                    f'{", ".join(changed)} no se puede modificar. '
                    f'Un recibo no se corrige.'
                )
            super().save(*args, **kwargs)

        self._loaded_frozen = _frozen_state(self, self.FROZEN_FIELDS)

    def clean(self):
        super().clean()
        if self._state.adding:
            self._prepare_receipt()

    # -- Borrado -----------------------------------------------------------

    def can_be_deleted(self) -> bool:
        # Un recibo gastó un número de la serie: misma regla que una factura
        # emitida. Ni física ni lógicamente.
        return False

    def delete(self, using=None, keep_parents=False):
        raise ProtectedRecordError(
            f'El cobro {self} no se puede borrar: es un recibo y consumió un '
            f'número de la serie. La devolución de un cobro será un documento '
            f'propio, todavía no implementado.'
        )
