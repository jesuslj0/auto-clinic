"""NIF / NIE / pasaporte del paciente: normalizar y validar.

El documento sale en las facturas, así que un NIF mal tecleado es un error que
llega impreso al paciente. Lo que se puede comprobar de verdad, se comprueba:

- **DNI** (8 cifras + letra) y **NIE** (X/Y/Z + 7 cifras + letra) llevan una letra
  de control calculable; si no cuadra, el documento está mal escrito.
- **Cualquier otra cosa** (pasaporte, documento de otro país, NIF especiales de
  menores y extranjeros) no tiene una regla común, y rechazarla impediría
  facturar a quien no tiene DNI. Se acepta con que sea alfanumérico y de largo
  razonable.
"""
import re

from django.core.exceptions import ValidationError

_CONTROL_LETTERS = 'TRWAGMYFPDXBNJZSQVHLCKE'
_DNI = re.compile(r'^\d{8}[A-Z]$')
_NIE = re.compile(r'^[XYZ]\d{7}[A-Z]$')
_OTHER = re.compile(r'^[A-Z0-9]{5,20}$')
_NIE_PREFIX = {'X': '0', 'Y': '1', 'Z': '2'}


def normalize_tax_id(value: str) -> str:
    """Mayúsculas y sin espacios, guiones, puntos ni barras: `12.345.678-z` → `12345678Z`."""
    return re.sub(r'[\s.\-/]', '', value or '').upper()


def _control_letter(digits: str) -> str:
    return _CONTROL_LETTERS[int(digits) % 23]


def validate_tax_id(value: str) -> None:
    """Valida un documento ya normalizado. Vacío es válido (el campo es opcional)."""
    if not value:
        return
    value = normalize_tax_id(value)

    if _DNI.match(value):
        if _control_letter(value[:8]) != value[8]:
            raise ValidationError(
                'La letra del DNI no coincide con sus cifras. Revisa que esté bien escrito.',
                code='invalid_dni',
            )
        return

    if _NIE.match(value):
        digits = _NIE_PREFIX[value[0]] + value[1:8]
        if _control_letter(digits) != value[8]:
            raise ValidationError(
                'La letra del NIE no coincide con sus cifras. Revisa que esté bien escrito.',
                code='invalid_nie',
            )
        return

    if not _OTHER.match(value):
        raise ValidationError(
            'Escribe un NIF, NIE o pasaporte válido: entre 5 y 20 letras o cifras, sin símbolos.',
            code='invalid_tax_id',
        )
