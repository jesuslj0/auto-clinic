"""Vistas de la capa clínica. Ahora mismo, solo una: servir una foto.

Esta capa **no tiene API REST** a propósito (ver `clinical/README.md`), y este
endpoint no la abre: es una vista de sesión del panel, no un recurso de DRF, y no
devuelve dato clínico alguno más allá de una redirección a la foto pedida. El
token `Api-Key` del agente no autentica aquí, y aunque autenticara,
`can_view_patient()` lo rechaza explícitamente.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View

from clinical.attachments import log_attachment_download, signed_url_for
from clinical.models import LesionAttachment


class LesionAttachmentDownloadView(LoginRequiredMixin, View):
    """Redirige a la URL firmada de un adjunto, tras comprobar el permiso.

    El orden importa y es el único posible: se comprueba quién pide
    (`signed_url_for` lanza `PermissionDenied` → 403), se registra el acceso en
    `AccessLog` y solo entonces se redirige. Django nunca sirve el fichero: lo
    entrega el bucket, contra una URL firmada que caduca en minutos.

    Se identifica por `public_id` (un UUID) y no por la PK: un id secuencial
    invita a tantear el de al lado, y aunque el 403 lo pararía, la existencia de
    la fila es de por sí información sobre un paciente.
    """

    def get(self, request, public_id):
        attachment = get_object_or_404(
            LesionAttachment.objects.select_related(
                'observation__lesion__episode__history'
            ),
            public_id=public_id,
        )
        url = signed_url_for(attachment, request.user)
        log_attachment_download(attachment, request=request)

        response = HttpResponseRedirect(url)
        # La redirección lleva una URL firmada: ni el navegador ni un proxy
        # intermedio deben quedársela.
        response['Cache-Control'] = 'private, no-store, max-age=0'
        return response
