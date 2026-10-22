"""La firma que llega del lienzo: qué se acepta y qué se rechaza.

El JavaScript del navegador avisa de un lienzo vacío, pero no es un control: la
comprobación que cuenta es la del servidor, que decodifica la imagen y mide la
tinta. Aquí se defiende que no se fía de lo que dice el cliente.
"""
import base64
from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from PIL import Image, ImageDraw

from clinical.signature import (
    DATA_URL_PREFIX,
    EMPTY_MESSAGE,
    INVALID_MESSAGE,
    signature_from_data_url,
)


def data_url(image, fmt='PNG', prefix=DATA_URL_PREFIX):
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return prefix + base64.b64encode(buffer.getvalue()).decode()


def signed_canvas(size=(400, 160), transparent=False):
    """Un lienzo con un trazo de verdad, como el que exporta el navegador."""
    background = (255, 255, 255, 0) if transparent else (255, 255, 255)
    image = Image.new('RGBA' if transparent else 'RGB', size, background)
    draw = ImageDraw.Draw(image)
    draw.line([(20, 120), (120, 30), (220, 110), (360, 40)], fill=(15, 23, 42), width=5)
    return image


def test_a_real_stroke_becomes_a_png_file():
    upload = signature_from_data_url(data_url(signed_canvas()))

    assert upload.name == 'firma.png'
    assert upload.content_type == 'image/png'
    assert Image.open(BytesIO(upload.read())).format == 'PNG'


def test_the_saved_file_is_rewritten_from_pixels():
    """No se guardan los bytes del cliente: se reescribe un PNG propio."""
    original = data_url(signed_canvas())
    upload = signature_from_data_url(original)

    assert base64.b64decode(original[len(DATA_URL_PREFIX):]) != upload.read()


def test_a_transparent_canvas_is_flattened_on_white():
    """Una firma negra sobre transparente se vería negro sobre negro en un visor oscuro."""
    upload = signature_from_data_url(data_url(signed_canvas(transparent=True)))
    image = Image.open(BytesIO(upload.read()))

    assert image.mode == 'RGB'
    assert image.getpixel((2, 2)) == (255, 255, 255)


@pytest.mark.parametrize('value', ['', '   ', None])
def test_empty_is_rejected_with_the_friendly_message(value):
    with pytest.raises(ValidationError) as error:
        signature_from_data_url(value)

    assert error.value.messages == [EMPTY_MESSAGE]


def test_a_blank_canvas_is_rejected():
    blank = Image.new('RGB', (400, 160), (255, 255, 255))

    with pytest.raises(ValidationError) as error:
        signature_from_data_url(data_url(blank))

    assert error.value.messages == [EMPTY_MESSAGE]


def test_a_single_dot_is_not_a_signature():
    image = Image.new('RGB', (400, 160), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((100, 60, 104, 64), fill=(0, 0, 0))

    with pytest.raises(ValidationError):
        signature_from_data_url(data_url(image))


@pytest.mark.parametrize('bad', [
    'data:image/jpeg;base64,/9j/4AAQSkZJRg==',
    'data:text/html;base64,PHNjcmlwdD4=',
    'https://example.com/firma.png',
    DATA_URL_PREFIX + '%%%no-es-base64%%%',
    DATA_URL_PREFIX + base64.b64encode(b'esto no es un png').decode(),
])
def test_anything_that_is_not_a_canvas_png_is_rejected(bad):
    with pytest.raises(ValidationError) as error:
        signature_from_data_url(bad)

    assert error.value.messages == [INVALID_MESSAGE]


def test_a_jpeg_dressed_as_png_is_rejected():
    """El prefijo del data URL lo escribe el cliente: se mira el contenido."""
    with pytest.raises(ValidationError):
        signature_from_data_url(data_url(signed_canvas(), fmt='JPEG'))


def test_an_oversized_image_is_rejected():
    huge = signed_canvas(size=(2500, 200))

    with pytest.raises(ValidationError) as error:
        signature_from_data_url(data_url(huge))

    assert error.value.messages == [INVALID_MESSAGE]
