"""
Tests del motor de disponibilidad (appointments/services.py).

Cubre:
  - Jornada partida: varios tramos el mismo día, sin slots en el hueco del mediodía.
  - Solapamiento de tramos → ValidationError.
  - Profesional con is_active=False → sin slots.
  - ProfessionalTimeOff → los slots dentro de la ausencia se excluyen.
  - buffer_minutes → los slots pegados al final de una cita se excluyen.
  - Contrato de la API: la respuesta mantiene su forma.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from appointments.models import (
    Appointment,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)
from appointments.services import get_professional_availability


def _next_weekday(target_weekday: int) -> datetime.date:
    """Próxima fecha futura cuyo weekday() == target_weekday (0=lunes)."""
    today = timezone.localdate()
    days_ahead = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


@pytest.fixture
def professional(db, admin_user):
    """El Professional que crea la señal al guardar admin_user."""
    return admin_user.professional_profile


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


def _local(target_date, hour, minute, tz):
    return datetime.datetime.combine(target_date, time(hour, minute), tzinfo=tz)


def _slot_times(availability, tz):
    """Horas locales 'HH:MM' de los slots devueltos."""
    return [slot.astimezone(tz).strftime('%H:%M') for slot in availability.slots]


# ---------------------------------------------------------------------------
# Jornada partida
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestJornadaPartida:
    def test_two_windows_same_day_are_allowed(self, professional):
        """La constraint permite dos tramos el mismo día si empiezan a horas distintas."""
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(16, 0), end_time=time(20, 0),
        )
        assert professional.schedules.filter(day_of_week=0).count() == 2

    def test_slots_in_both_windows_and_none_at_midday(self, professional, madrid):
        professional.slot_granularity_minutes = 60
        professional.save(update_fields=['slot_granularity_minutes'])
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(16, 0), end_time=time(20, 0),
        )

        availability = get_professional_availability(
            professional, _next_weekday(0), duration_minutes=60
        )
        horas = _slot_times(availability, madrid)

        assert availability.works_this_day is True
        # Mañana: 09–13 (14:00 no cabe). Tarde: 16–19 (20:00 no cabe).
        assert horas == ['09:00', '10:00', '11:00', '12:00', '13:00',
                         '16:00', '17:00', '18:00', '19:00']
        # Ni un solo hueco en el parón del mediodía.
        assert not any('14:00' <= h < '16:00' for h in horas)

    def test_schedule_bounds_span_both_windows(self, professional):
        """`schedule` en la respuesta cubre de la primera hora a la última."""
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(16, 0), end_time=time(20, 0),
        )
        availability = get_professional_availability(
            professional, _next_weekday(0), duration_minutes=30
        )
        assert availability.schedule_start == time(9, 0)
        assert availability.schedule_end == time(20, 0)


# ---------------------------------------------------------------------------
# Solapamiento de tramos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestScheduleOverlap:
    def test_overlapping_window_raises_validation_error(self, professional):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        conflicto = ProfessionalSchedule(
            professional=professional, day_of_week=0,
            start_time=time(13, 0), end_time=time(18, 0),
        )
        with pytest.raises(ValidationError) as exc:
            conflicto.full_clean()
        assert 'solapa' in str(exc.value).lower()

    def test_adjacent_windows_are_valid(self, professional):
        """14:00 justo después de un tramo que acaba a las 14:00 no es solapamiento."""
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        ProfessionalSchedule(
            professional=professional, day_of_week=0,
            start_time=time(14, 0), end_time=time(18, 0),
        ).full_clean()

    def test_inactive_window_does_not_block(self, professional):
        """Un tramo desactivado no cuenta como solapamiento."""
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0), is_active=False,
        )
        ProfessionalSchedule(
            professional=professional, day_of_week=0,
            start_time=time(10, 0), end_time=time(18, 0),
        ).full_clean()

    def test_end_before_start_raises(self, professional):
        with pytest.raises(ValidationError):
            ProfessionalSchedule(
                professional=professional, day_of_week=0,
                start_time=time(18, 0), end_time=time(9, 0),
            ).full_clean()


# ---------------------------------------------------------------------------
# Profesional dado de baja
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInactiveProfessional:
    def test_inactive_professional_has_no_slots(self, professional):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        professional.is_active = False
        professional.save(update_fields=['is_active'])

        availability = get_professional_availability(
            professional, _next_weekday(0), duration_minutes=30
        )
        assert availability.slots == []
        assert availability.works_this_day is False

    def test_inactive_professional_endpoint_returns_empty(self, admin_client, professional):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        professional.is_active = False
        professional.save(update_fields=['is_active'])

        lunes = _next_weekday(0)
        response = admin_client.get(
            f'/api/professionals/{professional.pk}/available-slots/?date={lunes.isoformat()}'
        )
        assert response.status_code == 200
        assert response.data['available_slots'] == []
        assert response.data['works_this_day'] is False


# ---------------------------------------------------------------------------
# Ausencias puntuales
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTimeOff:
    def test_slots_inside_time_off_are_excluded(self, professional, madrid):
        professional.slot_granularity_minutes = 60
        professional.save(update_fields=['slot_granularity_minutes'])
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        ProfessionalTimeOff.objects.create(
            professional=professional,
            starts_at=_local(lunes, 11, 0, madrid),
            ends_at=_local(lunes, 13, 0, madrid),
            reason=ProfessionalTimeOff.Reason.TRAINING,
        )

        availability = get_professional_availability(professional, lunes, duration_minutes=60)
        horas = _slot_times(availability, madrid)

        # 11:00 y 12:00 caen dentro de la formación.
        assert horas == ['09:00', '10:00', '13:00']

    def test_full_day_time_off_leaves_no_slots(self, professional, madrid):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        ProfessionalTimeOff.objects.create(
            professional=professional,
            starts_at=_local(lunes, 0, 0, madrid),
            ends_at=_local(lunes + timedelta(days=1), 0, 0, madrid),
            reason=ProfessionalTimeOff.Reason.VACATION,
        )
        availability = get_professional_availability(professional, lunes, duration_minutes=30)
        assert availability.slots == []
        # Sigue trabajando ese día de la semana: simplemente está de vacaciones.
        assert availability.works_this_day is True

    def test_time_off_on_another_day_does_not_affect(self, professional, madrid):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        ProfessionalTimeOff.objects.create(
            professional=professional,
            starts_at=_local(lunes + timedelta(days=1), 9, 0, madrid),
            ends_at=_local(lunes + timedelta(days=1), 14, 0, madrid),
        )
        availability = get_professional_availability(professional, lunes, duration_minutes=30)
        assert len(availability.slots) > 0

    def test_ends_before_starts_raises(self, professional, madrid):
        lunes = _next_weekday(0)
        with pytest.raises(ValidationError):
            ProfessionalTimeOff(
                professional=professional,
                starts_at=_local(lunes, 13, 0, madrid),
                ends_at=_local(lunes, 11, 0, madrid),
            ).full_clean()


# ---------------------------------------------------------------------------
# Buffer entre citas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBufferMinutes:
    def test_buffer_blocks_slot_right_after_appointment(
        self, professional, clinic_a, patient_a, service_a, madrid
    ):
        """Cita de 30 min a las 10:00 con buffer de 15 → 10:30 no, 10:45 sí."""
        professional.buffer_minutes = 15
        professional.save(update_fields=['buffer_minutes'])
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )

        lunes = _next_weekday(0)
        inicio = _local(lunes, 10, 0, madrid)
        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=inicio,
            end_at=inicio + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        availability = get_professional_availability(professional, lunes, duration_minutes=30)
        horas = _slot_times(availability, madrid)

        assert '10:30' not in horas  # cae dentro del buffer
        assert '10:45' in horas      # el buffer ya ha terminado
        assert '10:00' not in horas  # la cita en sí
        assert '09:45' not in horas  # 09:45 + 30 min pisaría la cita
        # El buffer va DESPUÉS de la cita: 09:30–10:00 encaja justo antes.
        assert '09:30' in horas

    def test_without_buffer_slot_right_after_is_free(
        self, professional, clinic_a, patient_a, service_a, madrid
    ):
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        inicio = _local(lunes, 10, 0, madrid)
        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=inicio,
            end_at=inicio + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        horas = _slot_times(
            get_professional_availability(professional, lunes, duration_minutes=30), madrid
        )
        assert '10:30' in horas

    def test_pending_appointment_blocks(
        self, professional, clinic_a, patient_a, service_a, madrid
    ):
        """Una cita 'pending' YA ocupa el hueco: reservar cierra el slot al instante.

        Lo que impide que lo ocupe indefinidamente sin que nadie la valide es el
        hold, no que el hueco siga ofreciéndose a otros pacientes.
        """
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        inicio = _local(lunes, 9, 0, madrid)
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=professional, scheduled_at=inicio,
            end_at=inicio + timedelta(minutes=30),
            status=Appointment.Status.PENDING,
        )
        horas = _slot_times(
            get_professional_availability(professional, lunes, duration_minutes=30), madrid
        )
        assert '09:00' not in horas

    def test_confirmed_appointment_blocks(
        self, professional, clinic_a, patient_a, service_a, madrid
    ):
        """Confirmar la cita de las 9:00 sí cierra el hueco."""
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        inicio = _local(lunes, 9, 0, madrid)
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=professional, scheduled_at=inicio,
            end_at=inicio + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        horas = _slot_times(
            get_professional_availability(professional, lunes, duration_minutes=30), madrid
        )
        assert '09:00' not in horas
        assert '09:15' not in horas  # pisaría la cita
        assert '09:30' in horas

    def test_cancelled_appointment_does_not_block(
        self, professional, clinic_a, patient_a, service_a, madrid
    ):
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        inicio = _local(lunes, 10, 0, madrid)
        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=inicio,
            end_at=inicio + timedelta(minutes=30),
            status=Appointment.Status.CANCELLED,
        )
        horas = _slot_times(
            get_professional_availability(professional, lunes, duration_minutes=30), madrid
        )
        assert '10:00' in horas


# ---------------------------------------------------------------------------
# start_hour / end_hour como filtro, no como fuente de verdad
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestHourFilter:
    def test_hour_filter_intersects_schedule(self, professional, madrid):
        professional.slot_granularity_minutes = 60
        professional.save(update_fields=['slot_granularity_minutes'])
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        availability = get_professional_availability(
            professional, _next_weekday(0), duration_minutes=60,
            start_hour=11, end_hour=13,
        )
        assert _slot_times(availability, madrid) == ['11:00', '12:00']

    def test_hour_filter_cannot_widen_schedule(self, professional, madrid):
        """Pedir 6:00–22:00 no saca huecos fuera del horario del profesional."""
        professional.slot_granularity_minutes = 60
        professional.save(update_fields=['slot_granularity_minutes'])
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(12, 0),
        )
        availability = get_professional_availability(
            professional, _next_weekday(0), duration_minutes=60,
            start_hour=6, end_hour=22,
        )
        assert _slot_times(availability, madrid) == ['09:00', '10:00', '11:00']


# ---------------------------------------------------------------------------
# Contrato de la API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAvailableSlotsContract:
    def test_response_shape_is_unchanged(self, admin_client, professional):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        response = admin_client.get(
            f'/api/professionals/{professional.pk}/available-slots/'
            f'?date={lunes.isoformat()}&duration=30'
        )

        assert response.status_code == 200
        assert set(response.data) == {
            'professional_id', 'professional_name', 'date',
            'works_this_day', 'schedule', 'duration_minutes', 'available_slots',
        }
        assert response.data['professional_id'] == professional.pk
        assert response.data['professional_name'] == str(professional)
        assert response.data['date'] == lunes.isoformat()
        assert response.data['duration_minutes'] == 30
        assert set(response.data['schedule']) == {'start_time', 'end_time'}

    def test_slots_are_iso_strings_with_clinic_offset(self, admin_client, professional):
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        lunes = _next_weekday(0)
        response = admin_client.get(
            f'/api/professionals/{professional.pk}/available-slots/'
            f'?date={lunes.isoformat()}&duration=30'
        )
        slots = response.data['available_slots']
        assert slots
        primero = slots[0]
        assert isinstance(primero, str)
        # ISO 8601 con offset de la clínica, p. ej. 2026-04-20T09:00:00+02:00
        assert primero.startswith(f'{lunes.isoformat()}T09:00:00')
        assert datetime.datetime.fromisoformat(primero).utcoffset() is not None

    def test_split_shift_slots_via_endpoint(self, admin_client, professional):
        """El endpoint refleja la jornada partida sin cambiar de forma."""
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(16, 0), end_time=time(20, 0),
        )
        lunes = _next_weekday(0)
        response = admin_client.get(
            f'/api/professionals/{professional.pk}/available-slots/'
            f'?date={lunes.isoformat()}&duration=30'
        )
        assert response.status_code == 200
        horas = [s[11:16] for s in response.data['available_slots']]
        assert '13:30' in horas   # último de la mañana
        assert '16:00' in horas   # primero de la tarde
        assert not any('14:00' <= h < '16:00' for h in horas)
