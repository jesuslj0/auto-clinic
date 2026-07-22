"""Un servicio de duración variable ocupa el máximo del rango en la agenda.

Es la única garantía de que dos citas seguidas no se pisen cuando la primera se
alarga hasta el tope del rango. La decisión vive en
`Service.booking_duration_minutes`, y estos tests la fijan de punta a punta:
desde el `end_at` que calcula `create_appointment` hasta el hueco que deja de
ofrecerse.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from appointments.models import Appointment, ProfessionalSchedule
from appointments.services import SlotUnavailable, create_appointment
from core.models import User
from services.models import Service


def _proximo_lunes() -> datetime.date:
    hoy = timezone.localdate()
    return hoy + timedelta(days=(0 - hoy.weekday()) % 7 or 7)


@pytest.fixture
def servicio_variable(db, clinic_a):
    """Limpieza de 30 a 60 minutos, 40 – 80 €."""
    return Service.objects.create(
        clinic=clinic_a,
        name='Limpieza',
        duration_minutes=30,
        duration_type=Service.ValueType.VARIABLE,
        duration_max_minutes=60,
        price='40.00',
        price_type=Service.ValueType.VARIABLE,
        price_max='80.00',
        is_active=True,
    )


@pytest.fixture
def prof(db, clinic_a, servicio_variable):
    user = User.objects.create_user(email='prof-variable@alpha.test', password='pass', clinic=clinic_a)
    professional = user.professional_profile
    professional.services.add(servicio_variable)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0, start_time=time(9, 0), end_time=time(14, 0),
    )
    return professional


@pytest.fixture
def lunes_10h(clinic_a):
    madrid = ZoneInfo(clinic_a.timezone)
    return datetime.datetime.combine(_proximo_lunes(), time(10, 0), tzinfo=madrid)


def _reservar(clinic, patient, service, cuando):
    return create_appointment(
        clinic=clinic, patient=patient, service=service, scheduled_at=cuando,
        require_online_booking=True, source=Appointment.Source.AGENT,
    )


@pytest.mark.django_db
class TestDuracionVariableEnAgenda:
    def test_la_cita_ocupa_el_maximo_del_rango(
        self, clinic_a, patient_a, servicio_variable, prof, lunes_10h
    ):
        cita = _reservar(clinic_a, patient_a, servicio_variable, lunes_10h)

        assert cita.end_at - cita.scheduled_at == timedelta(minutes=60)

    def test_el_hueco_del_minimo_queda_bloqueado(
        self, clinic_a, patient_a, servicio_variable, prof, lunes_10h
    ):
        _reservar(clinic_a, patient_a, servicio_variable, lunes_10h)

        # A las 10:30 la cita anterior sigue en curso: con duración fija de 30
        # min este hueco estaría libre.
        with pytest.raises(SlotUnavailable):
            _reservar(clinic_a, patient_a, servicio_variable, lunes_10h + timedelta(minutes=30))

    def test_el_hueco_siguiente_al_maximo_sigue_libre(
        self, clinic_a, patient_a, servicio_variable, prof, lunes_10h
    ):
        _reservar(clinic_a, patient_a, servicio_variable, lunes_10h)

        segunda = _reservar(clinic_a, patient_a, servicio_variable, lunes_10h + timedelta(minutes=60))

        assert segunda.pk is not None

    def test_sin_maximo_declarado_se_ocupa_el_minimo(
        self, clinic_a, patient_a, servicio_variable, prof, lunes_10h
    ):
        """Un servicio "desde 30 min" no tiene más información que el mínimo."""
        servicio_variable.duration_max_minutes = None
        servicio_variable.save(update_fields=['duration_max_minutes'])

        cita = _reservar(clinic_a, patient_a, servicio_variable, lunes_10h)

        assert cita.end_at - cita.scheduled_at == timedelta(minutes=30)
