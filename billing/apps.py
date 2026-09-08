from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'billing'
    verbose_name = 'Facturación'

    def ready(self):
        from audit import registry

        from billing.models import PatientInvoice, Payment

        # La factura cuelga de procedimientos clínicos y es justo el documento
        # que se discute años después: qué se cobró, cuándo y por cuánto. Se
        # audita entera —el nombre del servicio sale del catálogo y el importe
        # está congelado, no son texto libre—, salvo el motivo de anulación, que
        # sí lo es y puede contener detalle del paciente.
        #
        # `InvoiceSequence` NO se audita: es el contador, no el documento.
        registry.register(
            PatientInvoice,
            sensitive=['void_reason'],
            patient_resolver=lambda invoice: invoice.patient,
        )

        # El cobro, igual: nada de lo que guarda es texto libre ni dato clínico
        # —importe, método, fecha y número de recibo—, así que se registra
        # entero. Que un pago no se pueda borrar ni corregir hace del ChangeLog
        # la única forma de saber cuándo se registró y por quién.
        #
        # `ReceiptSequence` NO se audita: es el contador, no el documento.
        registry.register(
            Payment,
            patient_resolver=lambda payment: payment.invoice.patient,
        )
