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


# --- Días no laborables: no ocupan columna ------------------------------------

import pytest  # noqa: E402
from django.urls import reverse  # noqa: E402

from appointments.models import Appointment, ProfessionalSchedule  # noqa: E402


@pytest.fixture
def lunes_a_viernes(professional_a):
    """Horario de lunes a viernes (0–4): sábado y domingo son no laborables."""
    ProfessionalSchedule.objects.filter(professional=professional_a).delete()
    for dow in range(5):
        ProfessionalSchedule.objects.create(
            professional=professional_a, day_of_week=dow,
            start_time=datetime.time(9), end_time=datetime.time(13),
        )
    return professional_a


def _week(client, user, week_start):
    client.force_login(user)
    return client.get(reverse('appointments:calendar'), {'week': week_start.isoformat()})


@pytest.mark.django_db
class TestDiasNoLaborables:
    def test_fin_de_semana_se_oculta_y_se_avisa(self, client, admin_user, lunes_a_viernes):
        monday = datetime.date(2030, 1, 7)

        response = _week(client, admin_user, monday)

        visible = [d['date'] for d in response.context['visible_days_info']]
        hidden = [d['date'] for d in response.context['hidden_days_info']]
        assert visible == [monday + datetime.timedelta(days=i) for i in range(5)]
        assert hidden == [monday + datetime.timedelta(days=5), monday + datetime.timedelta(days=6)]
        html = response.content.decode()
        assert 'Días no laborables ocultos' in html
        assert 'repeat(5, minmax(140px, 1fr))' in html

    def test_un_dia_no_laborable_con_cita_sigue_visible(
        self, client, admin_user, lunes_a_viernes, clinic_a, patient_a, service_a,
    ):
        monday = datetime.date(2030, 1, 7)
        saturday_10 = timezone.make_aware(datetime.datetime(2030, 1, 12, 10, 0))
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=lunes_a_viernes,
            scheduled_at=saturday_10, end_at=saturday_10 + datetime.timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        response = _week(client, admin_user, monday)

        visible = [d['date'] for d in response.context['visible_days_info']]
        assert datetime.date(2030, 1, 12) in visible
        assert [d['date'] for d in response.context['hidden_days_info']] == [datetime.date(2030, 1, 13)]

    def test_sin_horarios_no_se_oculta_nada(self, client, admin_user, professional_a):
        ProfessionalSchedule.objects.filter(professional=professional_a).delete()

        response = _week(client, admin_user, datetime.date(2030, 1, 7))

        assert len(response.context['visible_days_info']) == 7
        assert response.context['hidden_days_info'] == []


def test_a_lone_appointment_is_wide_and_overlapping_ones_are_not():
    solo = _Appt(_at(9), 30)
    a, b = _Appt(_at(11), 30), _Appt(_at(11), 30)
    AppointmentCalendarView._assign_overlap_columns([solo, a, b])
    assert solo.col_wide is True
    assert a.col_wide is b.col_wide is False


@pytest.mark.django_db
def test_la_card_lleva_el_nombre_del_paciente(client, admin_user, lunes_a_viernes, clinic_a, patient_a, service_a):
    start = timezone.make_aware(datetime.datetime(2030, 1, 8, 10, 0))
    Appointment.objects.create(
        clinic=clinic_a, patient=patient_a, service=service_a, professional=lunes_a_viernes,
        scheduled_at=start, end_at=start + datetime.timedelta(minutes=30),
        status=Appointment.Status.CONFIRMED,
    )

    html = _week(client, admin_user, datetime.date(2030, 1, 7)).content.decode()

    assert str(patient_a) in html
