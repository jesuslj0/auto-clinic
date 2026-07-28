"""Servido protegido de las fotos clínicas.

El bucket es privado: sin firma no se lee nada. Este módulo es quien decide a
quién se le firma, y la respuesta corta es «a quien pueda ver la ficha de ese
paciente, y al agente jamás».

La URL se genera **en cada petición** y caduca en minutos
(`querystring_expire`). No se guarda en ninguna parte: una URL firmada
persistida sería un enlace de acceso a dato de salud viviendo en la base de
datos, y el día que se filtrara la tabla se filtrarían también las fotos.

La comprobación de permisos y la generación de la URL están en la misma función
(`signed_url_for`) a propósito. Separarlas dejaría a mano una forma de firmar sin
comprobar, y esa es exactamente la equivocación que no puede caber aquí.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied

from audit.mixins import log_access
from audit.models import AccessLog


def can_view_patient(user, patient) -> bool:
    """`True` si `user` puede ver la documentación clínica de `patient`.

    Reglas, en orden:

    1. **El agente, nunca.** El token `Api-Key` de n8n no alcanza dato clínico
       por diseño (ver `clinical/README.md`); esta es la puerta por la que podría
       colarse, así que se cierra explícitamente y la primera.
    2. Un usuario no autenticado, tampoco.
    3. El superusuario ve todas las clínicas, como en el resto del proyecto.
    4. El resto, solo su clínica. Un usuario sin clínica asignada no ve nada:
       aquí el «sin clínica» del panel administrativo no puede significar
       «todas».
    """
    from core.authentication import ClinicAgent

    if isinstance(user, ClinicAgent):
        return False
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if patient is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if not getattr(user, 'clinic_id', None):
        return False
    return user.clinic_id == patient.clinic_id


def can_view_attachment(user, attachment) -> bool:
    """`True` si `user` puede ver ese adjunto.

    El permiso es el del paciente, alcanzado por la cadena
    adjunto → observación → lesión → episodio → historia → paciente. El adjunto
    no tiene permisos propios: quien puede ver la ficha puede ver sus fotos, y
    quien no, no.
    """
    return can_view_patient(user, attachment.patient)


def signed_url_for(attachment, user) -> str:
    """URL firmada y de vida corta del adjunto, o `PermissionDenied`.

    Es el ÚNICO camino hasta el contenido de una foto clínica. Comprueba primero
    y firma después; no hay variante que se salte el primer paso.
    """
    if not can_view_attachment(user, attachment):
        raise PermissionDenied(
            'No tiene permiso para ver los adjuntos clínicos de este paciente.'
        )
    # `.url` firma contra el backend en este mismo instante. El resultado no se
    # guarda ni se cachea: caduca, y una URL caducada guardada solo sirve para
    # filtrar por dónde estaba la foto.
    return attachment.file.url


def log_attachment_download(attachment, request=None):
    """Deja el acceso en `AccessLog`. Leer una foto clínica es un acceso a datos.

    Las lecturas no emiten señales, así que se declara a mano (ver
    `audit/README.md`). Sin esto, la única capa del proyecto que sirve imágenes
    de pacientes lo haría sin dejar rastro.
    """
    return log_access(
        action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
        obj=attachment,
        patient=attachment.patient,
        request=request,
    )
