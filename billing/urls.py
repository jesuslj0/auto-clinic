"""Rutas del panel de facturación.

Cada estado de la pantalla es una URL real —los filtros van en la query string,
no en la sesión ni en un POST—, así que se puede compartir, marcar como favorita
y recargar. htmx pide exactamente estas mismas direcciones y solo cambia la
plantilla que devuelve la vista.
"""
from django.urls import path

from billing.views import (
    InvoiceDeleteView,
    InvoiceIssueView,
    InvoicePaymentCreateView,
    InvoicePaymentFormView,
    InvoicePendingProceduresView,
    InvoiceProcedureView,
    InvoiceVoidView,
    PatientInvoiceCreateView,
    PatientInvoiceDetailView,
    PatientInvoiceListView,
)

app_name = 'billing'

urlpatterns = [
    path('', PatientInvoiceListView.as_view(), name='invoice-list'),
    # Alta a mano. Admite `?patient=<id>` para llegar desde la ficha de alguien
    # con el destinatario ya fijado.
    path('nueva/', PatientInvoiceCreateView.as_view(), name='invoice-create'),
    # Fragmento del formulario: lo que se le puede facturar a un paciente. Es
    # una URL propia porque la lista la decide el servidor, no el navegador.
    path(
        'pendientes/',
        InvoicePendingProceduresView.as_view(),
        name='invoice-pending-procedures',
    ),
    path('<int:pk>/', PatientInvoiceDetailView.as_view(), name='invoice-detail'),
    # Todo lo que cambia el documento va por POST: emitir gasta un número de la
    # serie y anular deja una factura sin efecto para siempre.
    path('<int:pk>/emitir/', InvoiceIssueView.as_view(), name='invoice-issue'),
    path('<int:pk>/anular/', InvoiceVoidView.as_view(), name='invoice-void'),
    # Cobrar también: gasta un número de la serie de recibos y no se deshace
    # (la devolución será un documento propio, todavía no implementado).
    path(
        '<int:pk>/cobrar/',
        InvoicePaymentCreateView.as_view(),
        name='invoice-payment',
    ),
    # Fragmento: el modal de cobro de una factura, para pedirlo desde el listado
    # sin dejar veinte formularios escritos en la tabla. Solo pinta; el POST que
    # mueve el dinero sigue siendo el de arriba.
    path(
        '<int:pk>/cobrar/formulario/',
        InvoicePaymentFormView.as_view(),
        name='invoice-payment-form',
    ),
    path(
        '<int:pk>/procedimientos/',
        InvoiceProcedureView.as_view(),
        name='invoice-procedure',
    ),
    path('<int:pk>/eliminar/', InvoiceDeleteView.as_view(), name='invoice-delete'),
]
