"""Rutas de la capa clínica.

Solo ficheros protegidos, y bajo autenticación de sesión. Cualquier ruta que se
añada aquí tiene que instrumentar `AccessLog` y quedar vedada al token del
agente.
"""
from django.urls import path

from clinical.views import LesionAttachmentDownloadView, SignedConsentSignatureView

app_name = 'clinical'

urlpatterns = [
    path(
        'attachments/<uuid:public_id>/',
        LesionAttachmentDownloadView.as_view(),
        name='lesion-attachment',
    ),
    path(
        'consents/<uuid:public_id>/signature/',
        SignedConsentSignatureView.as_view(),
        name='consent-signature',
    ),
]
