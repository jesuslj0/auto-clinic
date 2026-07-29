"""Filtros de presentación de la ficha del paciente.

Mismo patrón que `appointments/templatetags/appointment_extras.py`: aquí vive lo
que es decisión de *cómo se enseña* un dato, no de cómo se guarda. Ninguno de
estos filtros devuelve HTML: devuelven las piezas y la plantilla decide con qué
estilo pinta cada una.
"""
from decimal import Decimal, InvalidOperation

from django import template
from django.utils.formats import number_format

from clinical.snapshots import is_answered

register = template.Library()


# ---------------------------------------------------------------------------
# Teléfonos
# ---------------------------------------------------------------------------
#
# Los teléfonos se guardan en E.164 (`+34687957499`, ver
# `patients/services.py::normalize_phone`), y E.164 **no marca dónde acaba el
# prefijo de país**: los indicativos tienen entre uno y tres dígitos y no son
# autodelimitados. Sin `phonenumbers` (no está instalado, y no se va a instalar
# por un detalle de presentación) la única salida honesta es una tabla de
# indicativos conocidos.
#
# LIMITACIÓN ASUMIDA, no descuido: la tabla cubre España —el caso real de esta
# aplicación— más la Europa occidental, Latinoamérica y el Magreb, que es de
# donde vienen los teléfonos extranjeros de una clínica española. Un indicativo
# que no esté aquí **no se parte**: se enseña el número entero tal cual. Partir
# mal («+2 12…» en vez de «+212 …») desinforma; no partir solo es menos bonito.
# Ampliar la tabla es añadir una línea.
COUNTRY_CODES = frozenset({
    # Un dígito
    '1',                                             # Norteamérica (NANP)
    '7',                                             # Rusia y Kazajistán
    # Dos dígitos
    '20', '27',                                      # Egipto, Sudáfrica
    '30', '31', '32', '33', '34', '36', '39',        # Grecia…España…Italia
    '40', '41', '43', '44', '45', '46', '47', '48', '49',
    '51', '52', '53', '54', '55', '56', '57', '58',  # Latinoamérica
    '60', '61', '62', '63', '64', '65', '66',
    '81', '82', '84', '86',
    '90', '91', '92', '93', '94', '95', '98',
    # Tres dígitos
    '212', '213', '216', '218',                      # Magreb
    '351', '352', '353', '354', '355', '356', '357', '358', '359',
    '376', '377', '380', '385', '386',               # Andorra, Mónaco…
    '420', '421',                                    # Chequia, Eslovaquia
    '501', '502', '503', '504', '505', '506', '507', '509',
    '591', '592', '593', '595', '597', '598',
    '971', '972',
})


def _group_national(digits: str) -> str:
    """Agrupa el número nacional: pares desde el final y el resto delante.

    Para los nueve dígitos españoles esta regla general da exactamente el
    3-2-2-2 de toda la vida (`657 38 49 49`), así que no hace falta un caso
    especial para España. Con otras longitudes el resultado no será el formato
    canónico del país, pero es legible y nunca revienta: es agrupar, no
    interpretar.
    """
    groups = []
    rest = digits
    while len(rest) > 3:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    groups.insert(0, rest)
    return ' '.join(groups)


def split_phone(value) -> dict:
    """Parte un teléfono E.164 en `{'prefix': '+34', 'number': '687 95 74 99'}`.

    Ante cualquier duda devuelve el valor tal cual en `number` y el prefijo
    vacío: un teléfono vacío, con basura, sin `+` o con un indicativo que no
    reconocemos sale entero y sin tocar.
    """
    raw = str(value).strip() if value else ''
    if not raw.startswith('+'):
        return {'prefix': '', 'number': raw}

    digits = raw[1:]
    if not digits.isdigit():
        return {'prefix': '', 'number': raw}

    # De más largo a más corto: el indicativo más específico manda («+351…» es
    # Portugal, no «+35» seguido de un 1).
    for size in (3, 2, 1):
        code = digits[:size]
        if len(digits) > size and code in COUNTRY_CODES:
            return {'prefix': f'+{code}', 'number': _group_national(digits[size:])}

    return {'prefix': '', 'number': raw}


@register.filter
def phone_parts(value):
    """Devuelve las dos piezas del teléfono para pintarlas con estilos distintos.

    Uso:

        {% with tel=patient.phone|phone_parts %}
            <span class="text-content-subtle">{{ tel.prefix }}</span>
            <span class="text-content">{{ tel.number }}</span>
        {% endwith %}
    """
    return split_phone(value)


# ---------------------------------------------------------------------------
# Importes
# ---------------------------------------------------------------------------

@register.filter
def euros(value):
    """Importe en euros con formato español: `1.234,50 €`.

    El espacio antes del símbolo es duro a propósito: el importe y su moneda no
    se parten en dos líneas.
    """
    if value is None or value == '':
        return '—'
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    formatted = number_format(amount, decimal_pos=2, use_l10n=True, force_grouping=True)
    return f'{formatted} €'


# ---------------------------------------------------------------------------
# Anamnesis
# ---------------------------------------------------------------------------

@register.filter
def answer_display(entry):
    """Respuesta de una entrada del snapshot, lista para leer.

    `entry` es un dict del `snapshot` de un `QuestionnaireResponse` (ver
    `clinical/snapshots.py`), nunca una `Question` viva. Devuelve cadena vacía
    si no se contestó, para que la plantilla pueda distinguir «no» de «sin
    responder»: `False` y `0` SÍ son respuestas clínicas.
    """
    if not isinstance(entry, dict) or not is_answered(entry):
        return ''

    answer = entry.get('answer')
    if isinstance(answer, bool):
        return 'Sí' if answer else 'No'
    if isinstance(answer, (list, tuple)):
        return ', '.join(str(item) for item in answer)
    return str(answer)
