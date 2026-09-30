"""Foto de perfil del paciente: dónde se guarda, qué se acepta y cómo se sirve.

Una foto de la cara de un paciente, en la ficha de una clínica, identifica a
alguien que recibe atención sanitaria. Por eso sigue las mismas reglas que las
fotos clínicas aunque no sea un documento clínico:

- **Bucket privado** (`clinical_media`), prefijo propio `patient-photos/`, clave
  UUID. Se guarda la clave del objeto, nunca una URL.
- **Validada por contenido** con `validate_clinical_image()` (tamaño → firma →
  decodificación real) y **reescrita desde los píxeles**: sin EXIF, sin GPS, sin
  nada que no sea la imagen. Se recorta a un cuadrado y se reduce: es un avatar,
  no hace falta guardar la foto de 12 MP del móvil.
- **Solo se sirve firmada** (caché privada del navegador de 4 minutos, menos que
  la firma), tras comprobar que quien pide puede ver al paciente
  (`signed_photo_url`), y cada vista deja un `AccessLog`.

A diferencia de una foto clínica, esta SÍ se reemplaza y se quita: no es parte
de la historia, y guardar las anteriores no tiene ninguna finalidad. Al cambiarla
se borra el objeto antiguo del bucket (minimización de datos).
"""
from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import storages

from clinical.files import clinical_media_storage, validate_clinical_image

# `audit` y `clinical.attachments` se importan dentro de las funciones: este
# módulo lo carga `patients.models`, y a esa hora el registro de apps aún no
# admite importar otros modelos.

#: Lado del cuadrado final, en píxeles. Sobra para cualquier avatar del panel.
PHOTO_SIZE = 512
PHOTO_MIME_TYPE = 'image/jpeg'
PHOTO_URL_EXPIRE = 300
#: Caché privada del navegador, siempre por debajo de la vida de la firma. El
#: avatar se pinta en listas que se repintan en vivo (chats, directorio): sin
#: caché, cada repintado pediría otra vez todas las fotos y dejaría un
#: `AccessLog` por cada una. Nada de caché compartida (`private`): ni proxies
#: ni CDN se quedan la foto. Las plantillas añaden `?v=<updated_at>` a la URL,
#: así que una foto nueva nunca se tapa con la anterior en caché.
PHOTO_CACHE_CONTROL = 'private, max-age=240'


@dataclass(frozen=True)
class PreparedPhoto:
    content: bytes
    checksum: str


def patient_photo_upload_to(instance, filename):
    """Clave de la foto: un UUID, sin rastro del nombre original ni del paciente."""
    key = uuid.uuid4().hex
    return f'patient-photos/{key[:2]}/{key}.jpg'


def prepare_patient_photo(file) -> PreparedPhoto:
    """Valida la foto y la devuelve recortada, reducida y sin metadatos.

    Siempre sale un JPEG: un avatar no necesita transparencia, y un solo formato
    de salida es una cosa menos que puede salir mal al pintarla.
    """
    from PIL import Image, ImageOps

    validate_clinical_image(file)
    try:
        file.seek(0)
        with Image.open(file) as source:
            source.load()
            # La orientación del EXIF se aplica antes de tirarlo: si no, las
            # fotos del móvil saldrían tumbadas.
            image = ImageOps.exif_transpose(source)
            image = ImageOps.fit(
                image.convert('RGB'), (PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS
            )
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=88, optimize=True)
    except Exception as exc:  # Pillow lanza de todo con ficheros raros.
        raise ValidationError('No se ha podido procesar la imagen.') from exc
    finally:
        file.seek(0)

    content = output.getvalue()
    return PreparedPhoto(
        content=content, checksum=f'sha256:{hashlib.sha256(content).hexdigest()}'
    )


def signed_photo_url(patient, user) -> str:
    """URL firmada y de vida corta de la foto, o `PermissionDenied`.

    Comprueba y firma en la misma función: no hay forma de firmar sin comprobar.
    """
    from clinical.attachments import can_view_patient

    if not patient.photo or not can_view_patient(user, patient):
        raise PermissionDenied('No tiene permiso para ver la foto de este paciente.')

    backend = storages['clinical_media']
    name = patient.photo.name
    try:
        from storages.backends.s3 import S3Storage
    except ImportError:  # pragma: no cover - django-storages está en requirements
        S3Storage = None

    if S3Storage is not None and isinstance(backend, S3Storage):
        return backend.url(
            name,
            parameters={
                'ResponseCacheControl': PHOTO_CACHE_CONTROL,
                'ResponseContentType': PHOTO_MIME_TYPE,
                'ResponseContentDisposition': 'inline',
            },
            expire=PHOTO_URL_EXPIRE,
        )
    # Otros backends (tests en memoria) no firman.
    return backend.url(name)


def log_photo_view(patient, request=None):
    """Ver la foto es un acceso a los datos del paciente: queda en `AccessLog`."""
    from audit.mixins import log_access
    from audit.models import AccessLog

    return log_access(
        action=AccessLog.Action.DOWNLOAD_ATTACHMENT,
        obj=patient,
        patient=patient,
        request=request,
    )


def delete_photo_object(name: str) -> None:
    """Borra del bucket una foto que ya no usa nadie. Nunca falla hacia fuera.

    Se llama con `transaction.on_commit`: si el guardado se deshace, la ficha
    seguiría apuntando a la foto antigua y no puede haber desaparecido.
    """
    if not name:
        return
    try:
        storages['clinical_media'].delete(name)
    except Exception:  # pragma: no cover - un objeto huérfano no rompe la ficha
        pass
