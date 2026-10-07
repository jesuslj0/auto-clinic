"""Rutas de la capa clínica.

Solo ficheros protegidos, y bajo autenticación de sesión. Cualquier ruta que se
añada aquí tiene que instrumentar `AccessLog` y quedar vedada al token del
agente.
"""
from django.urls import path

from clinical.views import (
    LesionAttachmentDownloadView,
    ProcedureListView,
    ProcedurePatientPickerView,
    SignedConsentSignatureView,
)

app_name = 'clinical'

urlpatterns = [
    # Listado global de procedimientos (lectura clínica: lleva `AccessLog`).
    path('procedimientos/', ProcedureListView.as_view(), name='procedure-list'),
    # Primer paso del alta desde el listado: elegir paciente. El formulario es el
    # de la ficha (`patients:procedure-create`).
    path('procedimientos/nuevo/', ProcedurePatientPickerView.as_view(), name='procedure-new'),
    path(
        'adjuntos/<uuid:public_id>/',
        LesionAttachmentDownloadView.as_view(),
        name='lesion-attachment',
    ),
    path(
        'consentimientos/<uuid:public_id>/firma/',
        SignedConsentSignatureView.as_view(),
        name='consent-signature',
    ),
]
