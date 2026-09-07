"""Excepciones de facturación.

Todas señalan lo mismo desde ángulos distintos: **una factura emitida es un
documento cerrado**. Se emite una vez, no se corrige, y lo que se hace con ella
después es anularla y emitir otra.
"""


class InvoiceNotDraft(Exception):
    """Se intentó modificar una factura que ya no es un borrador.

    Añadir o quitar procedimientos, o volver a emitir: nada de eso ocurre sobre
    una factura emitida o anulada.
    """


class InvoiceNotIssued(Exception):
    """Se intentó anular una factura que no está emitida.

    Un borrador no se anula: se borra. Una anulada ya lo está.
    """


class EmptyInvoice(Exception):
    """Se intentó emitir una factura sin procedimientos.

    Una factura sin líneas no tiene importe que congelar ni nada que probar.
    """


class InvoiceFrozen(Exception):
    """Se intentó cambiar el contenido de una factura ya emitida.

    El número, la fecha de emisión, el importe y las líneas congeladas son el
    documento: no se reescriben.
    """


class InvoiceNotPayable(Exception):
    """Se intentó cobrar contra una factura que no está emitida.

    Un borrador todavía puede cambiar de importe y una anulada dejó de deber
    nada: ni contra uno ni contra otra entra dinero. Análoga a
    `InvoiceNotIssued`, pero mira desde el lado del cobro.
    """


class Overpayment(Exception):
    """Se intentó cobrar más de lo que debe una factura.

    La suma de los pagos vivos de una factura no puede superar su total. Cobrar
    de más no es un pago: es un error que habría que devolver, y aquí todavía no
    hay devoluciones.
    """


class InvoiceHasPayments(Exception):
    """Se intentó anular una factura que ya tiene cobros registrados.

    Anularla dejaría recibos apuntando a un documento sin efecto y dinero sin
    destino. Mientras no exista el reembolso, una factura cobrada no se anula.
    """


class PaymentFrozen(Exception):
    """Se intentó cambiar el contenido de un recibo ya registrado.

    Un pago nace confirmado y gasta un número de serie: el importe, el método,
    la fecha y el número son el documento y no se reescriben.
    """
