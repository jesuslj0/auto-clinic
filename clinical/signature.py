"""La firma manuscrita que llega del lienzo del navegador.

El lienzo (`static/js/signature_pad.js`) exporta un PNG como *data URL* en un
campo oculto del formulario. Aquí se convierte en un fichero y se decide si vale
como firma. Es todo lo que se hace con ella antes de `SignedConsent.sign()`: la
validación por contenido y el guardado en el bucket privado siguen siendo del
modelo (`SignedConsent.save()`), por la vía de siempre.

El navegador no es de fiar: la comprobación del lienzo vacío del JavaScript es
una comodidad, y la que cuenta es la de aquí. Por eso se **decodifica** la imagen
y se mira cuánta tinta lleva; no se confía en el prefijo del data URL ni en el
tamaño declarado. Y se **reescribe** desde los píxeles: lo que se guarda es un
PNG propio, no los bytes que mandó el cliente.
"""
from __future__ import annotations

import base64
import binascii
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, UnidentifiedImageError

DATA_URL_PREFIX = 'data:image/png;base64,'

#: Tope del campo antes de decodificar nada. Una firma de lienzo pesa decenas de
#: KB; esto deja margen de sobra para una pantalla grande sin aceptar un abuso.
MAX_DATA_URL_CHARS = 2_000_000

#: Dimensiones máximas, en píxeles. Un lienzo de tablet con pantalla de alta
#: densidad no pasa de aquí; más sería un fichero que no ha salido del lienzo.
MAX_WIDTH = 2400
MAX_HEIGHT = 1600

#: Píxeles que tienen que llevar tinta para contar como firma. Un toque suelto
#: con el dedo no es una firma, y un lienzo en blanco tampoco.
MIN_INK_PIXELS = 150

#: Por debajo de este nivel de gris (0 negro – 255 blanco) un píxel es tinta.
INK_THRESHOLD = 200

EMPTY_MESSAGE = 'Falta la firma del paciente: dibújala en el recuadro.'
INVALID_MESSAGE = 'La firma no se ha recibido bien. Bórrala y vuelve a firmar.'


def signature_from_data_url(data_url: str) -> SimpleUploadedFile:
    """Convierte el data URL del lienzo en el fichero PNG de la firma.

    Lanza `ValidationError` con un mensaje para el usuario si está vacío, no es un
    PNG de lienzo o no lleva tinta suficiente.
    """
    data_url = (data_url or '').strip()
    # El lienzo sin tocar no manda nada; una cabecera sin datos es lo mismo.
    if not data_url or data_url == DATA_URL_PREFIX:
        raise ValidationError(EMPTY_MESSAGE)
    if len(data_url) > MAX_DATA_URL_CHARS or not data_url.startswith(DATA_URL_PREFIX):
        raise ValidationError(INVALID_MESSAGE)

    try:
        raw = base64.b64decode(data_url[len(DATA_URL_PREFIX):], validate=True)
        image = Image.open(BytesIO(raw))
        # El tamaño se lee de la cabecera, antes de decodificar los píxeles.
        if image.format != 'PNG' or image.width > MAX_WIDTH or image.height > MAX_HEIGHT:
            raise ValidationError(INVALID_MESSAGE)
        image.load()
    except ValidationError:
        raise
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError(INVALID_MESSAGE)

    # Sobre fondo blanco: un PNG con transparencia se vería negro sobre negro en
    # cualquier visor oscuro, y una firma que no se ve no prueba nada.
    flattened = Image.new('RGB', image.size, (255, 255, 255))
    flattened.paste(image.convert('RGBA'), mask=image.convert('RGBA').split()[3])

    histogram = flattened.convert('L').histogram()
    if sum(histogram[:INK_THRESHOLD]) < MIN_INK_PIXELS:
        raise ValidationError(EMPTY_MESSAGE)

    buffer = BytesIO()
    flattened.save(buffer, format='PNG', optimize=True)
    return SimpleUploadedFile('firma.png', buffer.getvalue(), content_type='image/png')
