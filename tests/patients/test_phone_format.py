"""Formato de presentación del teléfono (y del importe) en la ficha.

Los teléfonos se guardan en E.164 (`+34687957499`), que es lo correcto para
guardar y horrible para leer. El filtro parte el prefijo del número y agrupa los
dígitos; la plantilla los pinta con distinto peso.

E.164 no dice dónde acaba el prefijo de país, así que el filtro tira de una tabla
de indicativos conocidos. Lo que se prueba aquí, además del caso español, es la
otra mitad de la decisión: **lo que no se reconoce no se parte**.
"""
from decimal import Decimal

import pytest
from django.template import Context, Template
from django.urls import reverse

from patients.models import Patient
from patients.templatetags.patient_extras import euros, phone_parts, split_phone


@pytest.mark.parametrize('raw, prefix, number', [
    # España: nueve dígitos en 3-2-2-2, que es como se dicta en voz alta.
    ('+34687957499', '+34', '687 95 74 99'),
    ('+34657384949', '+34', '657 38 49 49'),
    ('+34911223344', '+34', '911 22 33 44'),
    # Otros indicativos conocidos, de uno, dos y tres dígitos.
    ('+351912345678', '+351', '912 34 56 78'),
    ('+33612345678', '+33', '612 34 56 78'),
    ('+12025550143', '+1', '20 25 55 01 43'),
    # Longitud rara con prefijo conocido: se agrupa igual y no revienta.
    ('+3412345678', '+34', '12 34 56 78'),
    ('+34123', '+34', '123'),
])
def test_known_prefixes_are_split_and_grouped(raw, prefix, number):
    assert split_phone(raw) == {'prefix': prefix, 'number': number}


@pytest.mark.parametrize('raw', [
    '+9995551234',      # indicativo no asignado: mejor no partir que partir mal
    '+34',              # solo el prefijo, sin número detrás
    '555-0001',         # sin E.164 (dato heredado): sale tal cual
    'no es un teléfono',
    '+34 687 no válido',
])
def test_what_is_not_recognised_is_left_alone(raw):
    """Ante la duda, el valor entero en `number` y ningún prefijo inventado."""
    assert split_phone(raw) == {'prefix': '', 'number': raw}


@pytest.mark.parametrize('raw', ['', None, '   '])
def test_empty_phone_does_not_break(raw):
    assert split_phone(raw) == {'prefix': '', 'number': ''}


def test_filter_is_registered_and_usable_from_a_template():
    rendered = Template(
        '{% load patient_extras %}'
        '{% with tel=phone|phone_parts %}{{ tel.prefix }}|{{ tel.number }}{% endwith %}'
    ).render(Context({'phone': '+34687957499'}))
    assert rendered == '+34|687 95 74 99'


def test_phone_parts_is_the_same_as_split_phone():
    assert phone_parts('+34687957499') == split_phone('+34687957499')


@pytest.mark.django_db
def test_the_patient_card_shows_the_phone_split(client, admin_user, clinic_a):
    """Extremo a extremo: el prefijo va suave y el número agrupado manda."""
    patient = Patient.objects.create(
        clinic=clinic_a, first_name='Ana', last_name='García',
        phone='+34687957499',
    )
    client.force_login(admin_user)
    html = client.get(reverse('patients:detail', kwargs={'id': patient.pk})).content.decode()

    assert '687 95 74 99' in html
    assert '+34687957499' not in html
    assert '<span class="text-content-subtle">+34</span>' in html


# ---------------------------------------------------------------------------
# Importes
# ---------------------------------------------------------------------------

# El separador de millares del locale `es` de Django es un espacio duro
# (`1 234,50`), que es además lo que recomienda la RAE. No se fuerza aquí: sale
# del locale, y por eso el test lo comprueba en vez de darlo por hecho.
@pytest.mark.parametrize('value, expected', [
    (Decimal('45.00'), '45,00\xa0€'),
    (Decimal('1234.5'), '1\xa0234,50\xa0€'),
    (0, '0,00\xa0€'),
    (None, '—'),
    ('', '—'),
])
def test_euros_uses_spanish_formatting(value, expected):
    assert euros(value) == expected
