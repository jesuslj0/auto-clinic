"""Adjuntos de WhatsApp: qué se acepta, cómo se limpia y con qué clave se guarda.

Una foto que manda un paciente —un corte, una uña, una herida— es dato de
salud. Se guarda con las mismas reglas que las fotos clínicas (ver
`clinical/files.py`): bucket privado `clinical_media`, clave UUID sin rastro del
paciente y validación por el CONTENIDO, nunca por la extensión ni por el
`Content-Type` que mande quien sube.

Lo que añade este módulo sobre `clinical/files.py`:

- **Limpieza de metadatos en toda imagen.** Se reescribe la imagen desde los
  píxeles, así que no sobrevive nada de lo que no es la foto: EXIF (GPS, modelo
  de móvil, fecha), XMP, IPTC, comentarios y fragmentos de texto de PNG. La
  orientación del EXIF se aplica ANTES de tirarlo, para que la foto no se quede
  girada. Vale para JPEG, PNG y WebP, incluidos los animados (stickers).
- **Audio**, que es otro tipo de documento con otras reglas, y por eso vive aquí
  y no se mete en la validación de fotos clínicas: lista blanca de Ogg
  (Opus/Vorbis, las notas de voz de WhatsApp) y MP3, comprobada por firma.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from clinical.files import ALLOWED_IMAGE_TYPES, validate_clinical_image

#: Audio admitido y extensión con la que se guarda. Lista BLANCA.
ALLOWED_AUDIO_TYPES = {
    'audio/ogg': '.ogg',
    'audio/mpeg': '.mp3',
}

#: WhatsApp limita el audio a 16 MB; no hay razón para aceptar más.
DEFAULT_AUDIO_MAX_BYTES = 16 * 1024 * 1024

_HEADER_BYTES = 64
_CHUNK_BYTES = 64 * 1024

#: Formato de Pillow con el que se reescribe cada tipo.
_PILLOW_FORMAT = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}


@dataclass(frozen=True)
class PreparedMedia:
    """Fichero listo para guardar: el contenido final y lo que se sabe de él."""

    content: bytes
    mime_type: str
    size_bytes: int
    checksum: str

    @property
    def extension(self) -> str:
        return {**ALLOWED_IMAGE_TYPES, **ALLOWED_AUDIO_TYPES}.get(self.mime_type, '')


def audio_max_bytes() -> int:
    return getattr(settings, 'CHAT_AUDIO_MAX_BYTES', DEFAULT_AUDIO_MAX_BYTES)


def chat_media_upload_to(instance, filename):
    """Clave de un adjunto de chat: `chat-media/<2>/<uuid><ext>`.

    Prefijo propio, separado de `lesion-attachments/` y `consent-signatures/`:
    ciclo de vida distinto, y así admite políticas de bucket propias sin mover
    objetos. El nombre original se ignora: lo elige quien sube.
    """
    import uuid

    key = uuid.uuid4().hex
    extension = {**ALLOWED_IMAGE_TYPES, **ALLOWED_AUDIO_TYPES}.get(instance.mime_type, '')
    return f'chat-media/{key[:2]}/{key}{extension}'


def _checksum(content: bytes) -> str:
    return f'sha256:{hashlib.sha256(content).hexdigest()}'


def _read_all(file) -> bytes:
    file.seek(0)
    chunks = []
    for chunk in iter(lambda: file.read(_CHUNK_BYTES), b''):
        chunks.append(chunk)
    file.seek(0)
    return b''.join(chunks)


# ---------------------------------------------------------------------------
# Imágenes
# ---------------------------------------------------------------------------

def strip_image_metadata(file, mime_type: str) -> bytes:
    """Reescribe la imagen desde sus píxeles y devuelve los bytes, sin metadatos.

    No se "borran" campos de un EXIF: se genera un fichero nuevo a partir de la
    imagen decodificada, con lo mínimo para pintarla bien. Así no depende de
    conocer cada tipo de metadato que pueda traer un móvil.

    Se conserva el perfil de color ICC (es cómo se ven los colores, no quién ni
    dónde) y la transparencia. Se aplica la orientación del EXIF antes de
    descartarlo.
    """
    from PIL import Image, ImageOps, ImageSequence

    image_format = _PILLOW_FORMAT[mime_type]
    file.seek(0)
    with Image.open(file) as source:
        source.load()
        icc_profile = source.info.get('icc_profile')
        output = io.BytesIO()
        params = {'icc_profile': icc_profile} if icc_profile else {}

        if getattr(source, 'is_animated', False) and getattr(source, 'n_frames', 1) > 1:
            # Stickers animados (WebP) o PNG animados: se copian los fotogramas,
            # que es solo imagen, y se descarta el resto.
            frames = [frame.copy() for frame in ImageSequence.Iterator(source)]
            durations = [frame.info.get('duration', 100) for frame in ImageSequence.Iterator(source)]
            frames[0].save(
                output,
                format=image_format,
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=source.info.get('loop', 0),
                **params,
            )
        else:
            image = ImageOps.exif_transpose(source)
            if image_format == 'JPEG':
                if image.mode not in ('RGB', 'L', 'CMYK'):
                    image = image.convert('RGB')
                params.update(quality=92, optimize=True)
            elif image_format == 'PNG':
                transparency = source.info.get('transparency')
                if transparency is not None and image.mode == source.mode:
                    params['transparency'] = transparency
                params['optimize'] = True
            elif image_format == 'WEBP':
                params['quality'] = 90
            image.save(output, format=image_format, **params)

    file.seek(0)
    return output.getvalue()


def prepare_chat_image(file) -> PreparedMedia:
    """Valida una foto de WhatsApp y la devuelve sin metadatos.

    Primero la validación clínica completa (tamaño con el límite de origen
    externo → firma → decodificación real). Solo lo que pasa se reescribe. El
    tamaño y el checksum que se guardan son los del fichero FINAL, el que de
    verdad está en el bucket.
    """
    probe = validate_clinical_image(file, external=True)
    try:
        content = strip_image_metadata(file, probe.mime_type)
    except Exception as exc:  # Pillow lanza de todo con ficheros raros.
        raise ValidationError('No se ha podido procesar la imagen.') from exc
    return PreparedMedia(
        content=content,
        mime_type=probe.mime_type,
        size_bytes=len(content),
        checksum=_checksum(content),
    )


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _sniff_audio(header: bytes):
    """Tipo de audio según los primeros bytes, o `None`."""
    if header.startswith(b'OggS'):
        # Un Ogg puede llevar vídeo (Theora). Solo se admite si la primera
        # página declara un códec de audio: Opus (notas de voz) o Vorbis.
        if b'OpusHead' in header or b'\x01vorbis' in header:
            return 'audio/ogg'
        return None
    if header.startswith(b'ID3'):
        return 'audio/mpeg'
    # MP3 sin etiqueta ID3: empieza directamente por una trama (11 bits a 1).
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        return 'audio/mpeg'
    return None


def is_chat_audio(file) -> bool:
    """`True` si el fichero parece audio por sus primeros bytes (no por el nombre).

    Solo decide por dónde validarlo: la validación de verdad la hace luego
    `prepare_chat_audio` o `prepare_chat_image`.
    """
    file.seek(0)
    header = file.read(_HEADER_BYTES)
    file.seek(0)
    return _sniff_audio(header) is not None


def prepare_chat_audio(file) -> PreparedMedia:
    """Valida una nota de voz o un audio de WhatsApp. Sin transformarlo."""
    content = _read_all(file)
    size = len(content)
    if size == 0:
        raise ValidationError('El fichero está vacío.')
    if size > audio_max_bytes():
        raise ValidationError(
            f'El audio ocupa {size} bytes y el máximo admitido es {audio_max_bytes()}.'
        )
    mime_type = _sniff_audio(content[:_HEADER_BYTES])
    if mime_type is None:
        raise ValidationError('El contenido del fichero no es un audio Ogg (Opus/Vorbis) o MP3.')
    return PreparedMedia(content=content, mime_type=mime_type, size_bytes=size, checksum=_checksum(content))
