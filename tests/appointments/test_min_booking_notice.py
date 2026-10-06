"""
Antelación mínima de reserva (`Clinic.min_booking_notice_minutes`, 120 por defecto).

  - Los huecos a menos de `ahora + plazo` no se ofrecen (profesional y clínica).
  - El hueco que empieza justo en el límite sí.
  - La vía online (agente/API) no puede saltárselo mandando la hora a mano,
    ni al crear ni al reprogramar; el staff sí puede.
  - 0 desactiva la regla.
  - Confirmar/cancelar una cita cercana no es "reservar con poca antelación".
"""
import datetime
from datetime import time
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from appointments.models import Appointment, ProfessionalSchedule
from appointments.services import (
    BookingTooSoon,
    create_appointment,
    get_clinic_available_slots,
    get_professional_availability,
    reschedule_appointment,
    validate_appointment_update,
)
from core.models import User


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes():
    hoy = timezone.localdate()
    return hoy + datetime.timedelta(days=(0 - hoy.weekday()) % 7 or 7)


@pytest.fixture
def prof(db, clinic_a, service_a):
    user = User.objects.create_user(email='notice@alpha.test', password='pass', clinic=clinic_a)
    professional = user.professional_profile
    professional.slot_granularity_minutes = 30
    professional.save(update_fields=['slot_granularity_minutes'])
    professional.services.add(service_a)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0, start_time=time(9, 0), end_time=time(18, 0),
    )
    return professional


@pytest.fixture
def a_las_10(monkeypatch, lunes, madrid):
    """Congela «ahora» en el lunes a las 10:00 hora de la clínica."""
    ahora = datetime.datetime.combine(lunes, time(10, 0), tzinfo=madrid)
    monkeypatch.setattr(timezone, 'now', lambda: ahora)
    return ahora


def _horas(slots, tz):
    return [s.astimezone(tz).strftime('%H:%M') for s in slots]


@pytest.mark.django_db
class TestOfferedSlots:
    def test_professional_slots_start_two_hours_from_now(self, prof, lunes, madrid, a_las_10):
        horas = _horas(get_professional_availability(prof, lunes, duration_minutes=60).slots, madrid)
        assert horas[0] == '12:00'
        assert '11:30' not in horas

    def test_zero_minutes_disables_the_rule(self, prof, clinic_a, lunes, madrid, a_las_10):
        clinic_a.min_booking_notice_minutes = 0
        clinic_a.save(update_fields=['min_booking_notice_minutes'])
        prof.refresh_from_db()
        horas = _horas(get_professional_availability(prof, lunes, duration_minutes=60).slots, madrid)
        assert horas[0] == '10:00'

    def test_clinic_slots_respect_the_notice(self, prof, clinic_a, lunes, madrid, a_las_10):
        slots = get_clinic_available_slots(
            clinic_a, lunes, duration_minutes=60, start_hour=8, end_hour=18,
        )
        assert _horas(slots, madrid)[0] == '12:00'


@pytest.mark.django_db
class TestOnlineBooking:
    def _crear(self, clinic, patient, service, cuando, **kwargs):
        return create_appointment(
            clinic=clinic, patient=patient, service=service, scheduled_at=cuando,
            require_online_booking=True, source=Appointment.Source.AGENT, **kwargs,
        )

    def test_online_booking_inside_the_window_is_rejected(
        self, clinic_a, patient_a, service_a, prof, a_las_10
    ):
        with pytest.raises(BookingTooSoon) as error:
            self._crear(clinic_a, patient_a, service_a, a_las_10 + datetime.timedelta(hours=1),
                        professional=prof)
        assert error.value.detail['code'] == 'booking_too_soon'
        assert not Appointment.objects.exists()

    def test_online_booking_exactly_at_the_limit_is_accepted(
        self, clinic_a, patient_a, service_a, prof, a_las_10
    ):
        cita = self._crear(clinic_a, patient_a, service_a, a_las_10 + datetime.timedelta(hours=2),
                           professional=prof)
        assert cita.pk

    def test_staff_can_book_inside_the_window(
        self, clinic_a, patient_a, service_a, prof, a_las_10
    ):
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=a_las_10 + datetime.timedelta(minutes=30),
            require_online_booking=False, source=Appointment.Source.STAFF, professional=prof,
        )
        assert cita.pk

    def test_online_reschedule_inside_the_window_is_rejected(
        self, clinic_a, patient_a, service_a, prof, a_las_10
    ):
        cita = self._crear(clinic_a, patient_a, service_a, a_las_10 + datetime.timedelta(hours=5),
                           professional=prof)
        with pytest.raises(BookingTooSoon):
            reschedule_appointment(cita, scheduled_at=a_las_10 + datetime.timedelta(minutes=45))
        cita.refresh_from_db()
        assert cita.scheduled_at == a_las_10 + datetime.timedelta(hours=5)

    def test_changing_status_of_a_close_appointment_is_not_blocked(
        self, clinic_a, patient_a, service_a, prof, a_las_10
    ):
        cita = self._crear(clinic_a, patient_a, service_a, a_las_10 + datetime.timedelta(hours=3),
                           professional=prof)
        # La hora llega a estar a 30 min: confirmar o cancelar sigue siendo válido.
        Appointment.objects.filter(pk=cita.pk).update(
            scheduled_at=a_las_10 + datetime.timedelta(minutes=30),
            end_at=a_las_10 + datetime.timedelta(minutes=60),
        )
        cita.refresh_from_db()
        validate_appointment_update(
            cita, {'status': Appointment.Status.CONFIRMED}, require_online_booking=True,
        )


@pytest.mark.django_db
class TestEarliestBookableEnLaApi:
    """La API de huecos dice cuál es el primer instante reservable.

    El agente lo usa para avisar de la antelación SOLO cuando lo que pide el
    paciente se queda fuera por ella, no como aviso general en cada consulta.
    """

    def test_devuelve_el_limite_en_la_zona_de_la_clinica(self, admin_client, prof, clinic_a, lunes, madrid):
        clinic_a.min_booking_notice_minutes = 120
        clinic_a.save()
        antes = timezone.now() + datetime.timedelta(minutes=120)

        response = admin_client.get(f'/api/professionals/{prof.pk}/available-slots/?date={lunes.isoformat()}')

        assert response.status_code == 200
        earliest = datetime.datetime.fromisoformat(response.data['earliest_bookable'])
        assert earliest.utcoffset() == antes.astimezone(madrid).utcoffset()
        assert abs((earliest - antes).total_seconds()) < 60
        assert response.data['min_notice_minutes'] == 120
