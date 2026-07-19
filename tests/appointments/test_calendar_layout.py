"""Reparto en columnas de citas solapadas en el calendario.

`AppointmentCalendarView._assign_overlap_columns` decide cuánto ancho ocupa cada
cita cuando varias caen en el mismo tramo, para que no se pinten una encima de
otra. Es lógica pura (no toca BD), así que se prueba con stubs.
"""
import datetime

from django.utils import timezone

from appointments.views import AppointmentCalendarView


class _Appt:
    """Lo mínimo que mira el algoritmo: hora de inicio y de fin."""

    def __init__(self, start, minutes):
        self.scheduled_at = start
        self._end = start + datetime.timedelta(minutes=minutes)

    def get_end_datetime(self):
        return self._end


def _at(hour, minute=0):
    return timezone.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_two_overlapping_split_in_half():
    a1, a2 = _Appt(_at(10), 30), _Appt(_at(10), 30)
    AppointmentCalendarView._assign_overlap_columns([a1, a2])
    assert a1.col_width == a2.col_width == '50.0'
    assert {a1.col_left, a2.col_left} == {'0.0', '50.0'}


def test_three_overlapping_split_in_thirds():
    citas = [_Appt(_at(10), 30) for _ in range(3)]
    AppointmentCalendarView._assign_overlap_columns(citas)
    assert {c.col_width for c in citas} == {'33.33'}
    assert {c.col_left for c in citas} == {'0.0', '33.33', '66.67'}


def test_non_overlapping_each_full_width():
    """Consecutivas sin solaparse: cada una ocupa todo el ancho."""
    a1 = _Appt(_at(10, 0), 30)   # 10:00–10:30
    a2 = _Appt(_at(10, 30), 30)  # 10:30–11:00
    AppointmentCalendarView._assign_overlap_columns([a1, a2])
    assert a1.col_width == a2.col_width == '100.0'
    assert a1.col_left == a2.col_left == '0.0'


def test_chained_overlap_reuses_freed_column():
    """a1 y a3 no se solapan entre sí, así que comparten columna: 2 columnas, no 3."""
    a1 = _Appt(_at(10, 0), 60)   # 10:00–11:00
    a2 = _Appt(_at(10, 30), 60)  # 10:30–11:30  (solapa a1)
    a3 = _Appt(_at(11, 15), 45)  # 11:15–12:00  (solapa a2, no a1)
    AppointmentCalendarView._assign_overlap_columns([a1, a2, a3])
    # 2 columnas → mitad de ancho cada una
    assert a1.col_width == a2.col_width == a3.col_width == '50.0'
    assert a1.col_left == '0.0'
    assert a2.col_left == '50.0'
    assert a3.col_left == '0.0'  # reutiliza la columna que dejó libre a1


def test_separate_clusters_are_independent():
    """Un solapamiento por la mañana no estrecha una cita suelta por la tarde."""
    manana = [_Appt(_at(9), 30), _Appt(_at(9), 30)]   # 2 columnas
    tarde = _Appt(_at(17), 30)                          # sola
    AppointmentCalendarView._assign_overlap_columns(manana + [tarde])
    assert manana[0].col_width == '50.0'
    assert tarde.col_width == '100.0'
