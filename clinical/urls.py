"""Rutas de la capa clínica.

Una sola, y bajo autenticación de sesión. Cualquier ruta que se añada aquí tiene
que instrumentar `AccessLog` y quedar vedada al token del agente.
"""
from django.urls import path

from clinical.views import LesionAttachmentDownloadView

app_name = 'clinical'

urlpatterns = [
    path(
        'attachments/<uuid:public_id>/',
        LesionAttachmentDownloadView.as_view(),
        name='lesion-attachment',
    ),
]
