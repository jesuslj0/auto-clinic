"""Adjuntos clínicos: dónde se guardan, con qué clave y qué se acepta.

Tres piezas, todas anteriores al modelo porque son las que hacen que un adjunto
no pueda entrar mal aunque entre por otro camino:

- **El backend** (`clinical_media_storage`). Un bucket privado, separado del
  media público. Se resuelve en cada operación y no al importar el modelo, de
  modo que cambiar `STORAGES` (en tests, o mañana en otro entorno) cambia de
  verdad dónde va el fichero.
- **La clave** (`attachment_upload_to`). Un UUID, nunca el nombre original.
  `foto_juan_perez_pie_izq.jpg` en la ruta de un objeto es un dato de salud
  escrito en un identificador: filtra quién es el paciente y qué le pasa a
  cualquiera que vea la clave, aunque el contenido esté protegido.
- **La validación** (`validate_clinical_image`). Qué ES el fichero, no qué dice
  su nombre. La extensión y el `Content-Type` del navegador los pone quien sube:
  no valen como control.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.files.storage import Storage, storages

from clinical.conf import attachment_max_bytes

#: Formatos admitidos y la extensión con la que se guarda cada uno. Lista
#: BLANCA: lo que no esté aquí se rechaza. Nada de PDF, ni HEIC, ni SVG (un SVG
#: es un documento con scripts, no una foto).
ALLOWED_IMAGE_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}

#: Formato que reporta Pillow → tipo MIME. Es la traducción del contenido real.
PILLOW_FORMAT_TO_MIME = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
}

#: Firmas de los formatos admitidos. Primer filtro, barato y sin decodificar.
_SIGNATURES = (
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
)

_HEADER_BYTES = 32
_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProbedFile:
    """Lo que se sabe del fichero después de mirarlo por dentro."""

    mime_type: str
    size_bytes: int
    checksum: str


class ClinicalMediaStorage(Storage):
    """Proxy al backend `clinical_media`, resuelto en cada operación.

    No implementa almacenamiento: reenvía todo al backend que digan los settings
    en ese momento. Existe porque `FileField(storage=...)` evalúa su argumento
    UNA sola vez, al importar el modelo; congelar ahí la instancia ataría el
    campo al backend del arranque y no habría forma de cambiarlo después
    (empezando por los tests, que guardan en memoria). Resolviendo en cada
    llamada, el campo escribe siempre donde toca.

    Los métodos de `Storage` se delegan uno a uno porque existen en la clase
    base y no llegarían a `__getattr__`; lo que no sea un método de la interfaz
    (`bucket_name`, `querystring_expire`…) sí cae por ahí.
    """

    alias = 'clinical_media'

    @property
    def backend(self):
        return storages[self.alias]

    def __getattr__(self, name):
        # Solo para lo que NO define `Storage`; los dunder se dejan pasar para
        # no confundir a copy/pickle con un objeto que parece tenerlo todo.
        if name.startswith('__'):
            raise AttributeError(name)
        return getattr(storages[self.alias], name)

    def __repr__(self):
        return f'<ClinicalMediaStorage alias={self.alias!r}>'

    # --- interfaz de Storage, delegada -------------------------------------

    def open(self, name, mode='rb'):
        return self.backend.open(name, mode)

    def save(self, name, content, max_length=None):
        return self.backend.save(name, content, max_length=max_length)

    def generate_filename(self, filename):
        return self.backend.generate_filename(filename)

    def get_valid_name(self, name):
        return self.backend.get_valid_name(name)

    def get_alternative_name(self, file_root, file_ext):
        return self.backend.get_alternative_name(file_root, file_ext)

    def get_available_name(self, name, max_length=None):
        return self.backend.get_available_name(name, max_length=max_length)

    def path(self, name):
        return self.backend.path(name)

    def delete(self, name):
        return self.backend.delete(name)

    def exists(self, name):
        return self.backend.exists(name)

    def listdir(self, path):
        return self.backend.listdir(path)

    def size(self, name):
        return self.backend.size(name)

    def url(self, name):
        return self.backend.url(name)

    def get_accessed_time(self, name):
        return self.backend.get_accessed_time(name)

    def get_created_time(self, name):
        return self.backend.get_created_time(name)

    def get_modified_time(self, name):
        return self.backend.get_modified_time(name)


def clinical_media_storage():
    """Backend de los adjuntos clínicos. Se pasa a `FileField(storage=...)`."""
    return ClinicalMediaStorage()


def attachment_upload_to(instance, filename):
    """Clave del objeto en el bucket: un UUID, sin rastro del nombre original.

    `filename` se ignora a propósito —es texto que elige quien sube— salvo por
    la extensión, que se deriva del MIME ya *validado* de la instancia y no de lo
    que traiga el nombre.

    El primer nivel de la ruta es un prefijo de dos caracteres del propio UUID:
    reparte las claves en vez de amontonar millones de objetos bajo el mismo
    prefijo, que es lo que degrada el listado en un almacén tipo S3.
    """
    extension = ALLOWED_IMAGE_TYPES.get(instance.mime_type, '')
    key = uuid.uuid4().hex
    return f'lesion-attachments/{key[:2]}/{key}{extension}'


def _file_size(file) -> int:
    """Tamaño del fichero sin fiarse de cabeceras: se mide."""
    try:
        return file.size
    except (AttributeError, TypeError):
        position = file.tell()
        file.seek(0, 2)
        size = file.tell()
        file.seek(position)
        return size


def _sniff_signature(header: bytes):
    """Tipo deducido de los primeros bytes, o `None` si no es de los nuestros."""
    for magic, mime_type in _SIGNATURES:
        if header.startswith(magic):
            return mime_type
    # WebP: 'RIFF' + 4 bytes de longitud + 'WEBP'.
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _real_mime_type(file):
    """Formato REAL del contenido, según Pillow. `None` si no es una imagen."""
    from PIL import Image, UnidentifiedImageError

    file.seek(0)
    try:
        with Image.open(file) as image:
            # `verify()` recorre el fichero y revienta si está corrupto o si no
            # es lo que dice ser; no basta con que `open()` no falle.
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    finally:
        file.seek(0)
    return PILLOW_FORMAT_TO_MIME.get(image_format)


def _checksum(file) -> str:
    """`sha256:<hexdigest>` del contenido, leído por bloques."""
    digest = hashlib.sha256()
    file.seek(0)
    for chunk in iter(lambda: file.read(_CHUNK_BYTES), b''):
        digest.update(chunk)
    file.seek(0)
    return f'sha256:{digest.hexdigest()}'


def validate_clinical_image(file, *, external: bool = False) -> ProbedFile:
    """Comprueba que el fichero es una foto clínica aceptable y la describe.

    Devuelve el tipo real, el tamaño y el checksum; lanza `ValidationError` con
    el motivo si no pasa. Se valida en este orden a propósito:

    1. **Tamaño**, que es lo único que se puede saber sin tocar el contenido, y
       evita decodificar 400 MB para descubrir después que sobran.
    2. **Firma de los primeros bytes**, filtro barato.
    3. **Decodificación real con Pillow**, que es la única comprobación que de
       verdad dice qué es el fichero. Un `.jpg` que sea un PDF, un ejecutable o
       un SVG con scripts muere aquí.

    Nunca se usa la extensión ni el `Content-Type` que manda el cliente: los
    pone quien sube el fichero, así que no son un control de nada.
    """
    max_bytes = attachment_max_bytes(external=external)
    size = _file_size(file)

    if size == 0:
        raise ValidationError('El fichero está vacío.')
    if size > max_bytes:
        raise ValidationError(
            f'El fichero ocupa {size} bytes y el máximo admitido es {max_bytes}.'
        )

    file.seek(0)
    header = file.read(_HEADER_BYTES)
    file.seek(0)
    if _sniff_signature(header) is None:
        raise ValidationError(
            'El contenido del fichero no es una imagen JPEG, PNG o WebP.'
        )

    mime_type = _real_mime_type(file)
    if mime_type is None or mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError(
            'El contenido del fichero no es una imagen JPEG, PNG o WebP.'
        )

    return ProbedFile(mime_type=mime_type, size_bytes=size, checksum=_checksum(file))
