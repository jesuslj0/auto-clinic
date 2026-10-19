"""Datos de una cita que una plantilla de recordatorio puede usar en sus variables.

Cada `{{n}}` de la plantilla se enlaza (`WhatsAppTemplate.variable_map`) con una
clave de esta lista cerrada, y aquí se calcula su valor para la cita concreta.

Quedan fuera a propósito el servicio y cualquier dato clínico: el mensaje pasa
por los servidores de Meta, y «Tratamiento de uña encarnada» es un dato de salud
que un recordatorio no necesita. Añadir uno aquí es una decisión de privacidad,
no un detalle técnico.
"""

from dataclasses import dataclass
from typing import Callable
from zoneinfo import ZoneInfo

from django.utils import formats, translation


class MissingReminderField(Exception):
    """La cita no tiene el dato que la plantilla pide (p. ej. no tiene profesional)."""

    def __init__(self, field):
        self.field = field
        super().__init__(f'La cita no tiene «{field.label.lower()}».')


@dataclass(frozen=True)
class ReminderField:
    key: str
    label: str
    example: str
    resolve: Callable


def _local(appointment):
    return appointment.scheduled_at.astimezone(ZoneInfo(appointment.clinic.timezone))


def _contact_name(appointment):
    if appointment.patient_id:
        return f'{appointment.patient.first_name} {appointment.patient.last_name}'.strip()
    if appointment.booked_for_other:
        # El teléfono es el de quien la pidió, así que a quien se saluda es a esa persona.
        return appointment.contact_name
    return appointment.patient_name


def _first_name(appointment):
    if appointment.patient_id and not appointment.booked_for_other:
        return appointment.patient.first_name
    return _contact_name(appointment).split(' ')[0]


def _date(appointment):
    with translation.override('es'):
        return formats.date_format(_local(appointment), r'l j \d\e F')


def _professional(appointment):
    return appointment.professional.display_name if appointment.professional_id else ''


def _address(appointment):
    clinic = appointment.clinic
    return ', '.join(part for part in (clinic.address, clinic.city) if part)


FIELDS = [
    # Los nombres nunca faltan: sin nombre se saluda al «paciente».
    ReminderField('patient_first_name', 'Nombre del paciente', 'Ana', lambda a: _first_name(a) or 'paciente'),
    ReminderField('patient_full_name', 'Nombre completo del paciente', 'Ana Pérez', lambda a: _contact_name(a) or 'paciente'),
    ReminderField('clinic_name', 'Nombre de la clínica', 'Tu clínica', lambda a: a.clinic.name),
    ReminderField('date', 'Fecha de la cita', 'lunes 6 de julio', _date),
    ReminderField('time', 'Hora de la cita', '10:30', lambda a: _local(a).strftime('%H:%M')),
    ReminderField('professional', 'Profesional', 'Dra. Lucía Martín', _professional),
    ReminderField('clinic_address', 'Dirección de la clínica', 'C/ Sierpes 12, Sevilla', _address),
]

BY_KEY = {field.key: field for field in FIELDS}

#: Orden con el que se preselecciona una plantilla sin mapeo: es el del borrador
#: de recordatorio (nombre, clínica, fecha y hora).
DEFAULT_ORDER = ['patient_first_name', 'clinic_name', 'date', 'time']


def default_map(variables):
    """Mapeo inicial sugerido: las primeras variables en el orden de `DEFAULT_ORDER`."""
    return {str(n): DEFAULT_ORDER[i] for i, n in enumerate(variables) if i < len(DEFAULT_ORDER)}


def resolve_values(appointment, variable_map, variables):
    """Valores de la cita, uno por variable y en su orden. Lanza `MissingReminderField`."""
    values = []
    for number in variables:
        field = BY_KEY[variable_map[str(number)]]
        value = ' '.join(str(field.resolve(appointment) or '').split())
        if not value:
            raise MissingReminderField(field)
        values.append(value)
    return values
