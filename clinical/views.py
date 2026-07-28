"""Vistas de la capa clínica: servir un fichero protegido, y nada más.

Esta capa **no tiene API REST** a propósito (ver `clinical/README.md`), y estos
endpoints no la abren: son vistas de sesión del panel, no recursos de DRF, y no
devuelven dato clínico alguno más allá de una redirección al fichero pedido. El
token `Api-Key` del agente no autentica aquí, y aunque autenticara,
`can_view_patient()` lo rechaza explícitamente.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View

from clinical.attachments import log_attachment_download, signed_url_for
from clinical.models import LesionAttachment, SignedConsent


class ProtectedFileRedirectView(LoginRequiredMixin, View):
    """Redirige a la URL firmada de un fichero clínico, tras comprobar el permiso.

    El orden importa y es el único posible: se comprueba quién pide
    (`signed_url_for` lanza `PermissionDenied` → 403), se registra el acceso en
    `AccessLog` y solo entonces se redirige. Django nunca sirve el fichero: lo
    entrega el bucket, contra una URL firmada que caduca en minutos.

    Se identifica por `public_id` (un UUID) y no por la PK: un id secuencial
    invita a tantear el de al lado, y aunque el 403 lo pararía, la existencia de
    la fila es de por sí información sobre un paciente.

    Las subclases solo dicen QUÉ se busca. El control —comprobar, registrar,
    redirigir— vive aquí una sola vez: repartido por vistas, es cuestión de
    tiempo que una se deje un paso.
    """

    def get_queryset(self):
        raise NotImplementedError

    def get(self, request, public_id):
        document = get_object_or_404(self.get_queryset(), public_id=public_id)
        url = signed_url_for(document, request.user)
        log_attachment_download(document, request=request)

        response = HttpResponseRedirect(url)
        # La redirección lleva una URL firmada: ni el navegador ni un proxy
        # intermedio deben quedársela.
        response['Cache-Control'] = 'private, no-store, max-age=0'
        return response


class LesionAttachmentDownloadView(ProtectedFileRedirectView):
    """Foto clínica de una observación de lesión."""

    def get_queryset(self):
        return LesionAttachment.objects.select_related(
            'observation__lesion__episode__history'
        )


class SignedConsentSignatureView(ProtectedFileRedirectView):
    """Firma manuscrita de un consentimiento.

    Una firma es dato personal pegado a un dato de salud, así que se sirve
    exactamente igual que una foto clínica: bucket privado, URL firmada de vida
    corta, permiso comprobado y `AccessLog`.
    """

    def get_queryset(self):
        return SignedConsent.objects.select_related('patient')
