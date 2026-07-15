"""Fase 2 del diseño A: `pending` bloquea, y el hold es lo que impide que bloquee para siempre.

Reservar cierra el hueco al instante. Si el staff no valida la cita dentro del
plazo (`clinic.hold_ttl_minutes`), `expire_appointment_holds` la cancela y el
hueco vuelve a ofrecerse.
"""
import datetime
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

from appointments.models import Appointment, AppointmentStatusHistory, ProfessionalSchedule
from appointments.services import (
    SlotUnavailable,
    confirm_by_clinic,
    create_appointment,
    get_professional_availability,
)
from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


def _next_weekday(target_weekday: int) -> datetime.date:
    today = timezone.localdate()
    return today + timedelta(days=(target_weekday - today.weekday()) % 7 or 7)


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes(madrid):
    return _next_weekday(0)


@pytest.fixture
def lunes_10h(lunes, madrid):
    return datetime.datetime.combine(lunes, time(10, 0), tzinfo=madrid)


@pytest.fixture
def prof(db, clinic_a, service_a):
    user = User.objects.create_user(email='prof@alpha.test', password='pass', clinic=clinic_a)
    professional = user.professional_profile
    professional.services.add(service_a)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0, start_time=time(9, 0), end_time=time(14, 0),
    )
    return professional


def _reservar(clinic, patient, service, cuando, **kwargs):
    return create_appointment(
        clinic=clinic, patient=patient, service=service, scheduled_at=cuando,
        require_online_booking=True, source=Appointment.Source.AGENT, **kwargs,
    )


# ---------------------------------------------------------------------------
# `pending` bloquea, en el motor de slots Y en la creación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPendingBlocks:
    def test_pending_appointment_disappears_from_available_slots(
        self, clinic_a, patient_a, service_a, prof, lunes, lunes_10h, madrid
    ):
        libres = lambda: [
            slot.astimezone(madrid).strftime('%H:%M')
            for slot in get_professional_availability(prof, lunes, duration_minutes=30).slots
        ]
        assert '10:00' in libres()

        _reservar(clinic_a, patient_a, service_a, lunes_10h)

        assert '10:00' not in libres()

    def test_the_engine_and_the_post_agree(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        """Si el motor no ofrece el hueco, el POST tampoco lo acepta. Y al revés."""
        _reservar(clinic_a, patient_a, service_a, lunes_10h)

        with pytest.raises(SlotUnavailable) as exc:
            _reservar(clinic_a, patient_a, service_a, lunes_10h)
        assert exc.value.detail['code'] == 'slot_unavailable'
        assert Appointment.objects.count() == 1

    def test_slot_unavailable_message_is_safe_to_show_the_patient(
        self, admin_client, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        _reservar(clinic_a, patient_a, service_a, lunes_10h)

        response = admin_client.post('/api/appointments/', {
            'clinic': clinic_a.pk, 'patient': patient_a.pk, 'service': service_a.pk,
            'scheduled_at': lunes_10h.isoformat(), 'status': 'pending',
        }, format='json')

        assert response.status_code == 400
        assert response.data['code'] == 'slot_unavailable'
        # Va directo al paciente por WhatsApp: sin IDs ni trazas.
        assert str(clinic_a.pk) not in response.data['message']
        assert str(prof.pk) not in response.data['message']

    def test_no_professional_available_is_still_a_different_error(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        """Sin nadie que preste el servicio no es que el hueco esté cogido."""
        from appointments.services import NoProfessionalAvailable

        with pytest.raises(NoProfessionalAvailable):
            _reservar(clinic_a, patient_a, service_a, lunes_10h)


# ---------------------------------------------------------------------------
# La carrera: dos conversaciones de WhatsApp por el mismo hueco
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestConcurrentBooking:
    def test_only_one_of_two_concurrent_bookings_wins(self):
        """Dos escrituras compitiendo de verdad, en dos conexiones a la vez.

        Sin el lock, ambas transacciones validan contra un hueco libre (ninguna ve
        la fila aún no commiteada de la otra) y ambas insertan: el paciente que
        pierde recibe igualmente un "tu cita está hecha". Con el lock, la segunda
        espera y se encuentra el hueco ya ocupado.
        """
        clinic = Clinic.objects.create(clinic_id='race', name='Race')
        service = Service.objects.create(
            clinic=clinic, name='Consulta', duration_minutes=30, price='10.00', is_active=True,
        )
        user = User.objects.create_user(email='race@prof.test', password='x', clinic=clinic)
        professional = user.professional_profile
        professional.services.add(service)
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        paciente = Patient.objects.create(
            clinic=clinic, first_name='A', last_name='B', phone='+34600000001',
        )
        cuando = datetime.datetime.combine(
            _next_weekday(0), time(10, 0), tzinfo=ZoneInfo(clinic.timezone)
        )

        def reservar():
            try:
                create_appointment(
                    clinic=clinic, patient=paciente, service=service, scheduled_at=cuando,
                    require_online_booking=True, source=Appointment.Source.AGENT,
                )
                return 'ok'
            except SlotUnavailable:
                return 'slot_unavailable'
            finally:
                # Cada hilo abre su propia conexión: hay que cerrarla a mano.
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = sorted(f.result() for f in [pool.submit(reservar), pool.submit(reservar)])

        assert resultados == ['ok', 'slot_unavailable']
        assert Appointment.objects.filter(professional=professional).count() == 1


# ---------------------------------------------------------------------------
# Caducidad de holds
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExpireHolds:
    def _expirar(self):
        salida = StringIO()
        call_command('expire_appointment_holds', stdout=salida)
        return salida.getvalue()

    def test_expired_hold_is_cancelled_and_the_slot_comes_back(
        self, clinic_a, patient_a, service_a, prof, lunes, lunes_10h, madrid
    ):
        cita = _reservar(clinic_a, patient_a, service_a, lunes_10h)

        libres = lambda: [
            slot.astimezone(madrid).strftime('%H:%M')
            for slot in get_professional_availability(prof, lunes, duration_minutes=30).slots
        ]
        assert '10:00' not in libres()

        cita.hold_expires_at = timezone.now() - timedelta(minutes=1)
        cita.save(update_fields=['hold_expires_at'])
        self._expirar()

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CANCELLED
        assert '10:00' in libres()  # el hueco vuelve a ofrecerse

    def test_expiry_goes_through_the_service_and_leaves_a_trace(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        cita = _reservar(clinic_a, patient_a, service_a, lunes_10h)
        cita.hold_expires_at = timezone.now() - timedelta(minutes=1)
        cita.save(update_fields=['hold_expires_at'])
        self._expirar()

        transicion = cita.status_history.latest('changed_at')
        assert transicion.to_status == Appointment.Status.CANCELLED
        assert transicion.actor == AppointmentStatusHistory.Actor.SYSTEM
        assert transicion.actor_label == 'Hold caducado'

    def test_hold_not_yet_expired_keeps_blocking(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        cita = _reservar(clinic_a, patient_a, service_a, lunes_10h)
        assert cita.hold_expires_at > timezone.now()

        self._expirar()

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.PENDING

    def test_confirmed_appointment_never_expires(
        self, clinic_a, patient_a, service_a, prof, lunes_10h, admin_user
    ):
        """Aunque arrastrara un hold: si la clínica la validó, no se cancela sola."""
        cita = _reservar(clinic_a, patient_a, service_a, lunes_10h)
        confirm_by_clinic(cita, user=admin_user)

        # Le forzamos un hold caducado a mano, que es el peor caso imaginable.
        Appointment.objects.filter(pk=cita.pk).update(
            hold_expires_at=timezone.now() - timedelta(days=1)
        )
        self._expirar()

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CONFIRMED

    def test_staff_appointment_never_expires(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
            require_online_booking=False, source=Appointment.Source.STAFF,
        )
        assert cita.hold_expires_at is None

        self._expirar()

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CONFIRMED

    def test_command_is_idempotent(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        cita = _reservar(clinic_a, patient_a, service_a, lunes_10h)
        cita.hold_expires_at = timezone.now() - timedelta(minutes=1)
        cita.save(update_fields=['hold_expires_at'])

        primera = self._expirar()
        segunda = self._expirar()

        assert '1 citas caducadas' in primera
        assert '0 citas caducadas' in segunda
        assert cita.status_history.filter(
            to_status=Appointment.Status.CANCELLED
        ).count() == 1
