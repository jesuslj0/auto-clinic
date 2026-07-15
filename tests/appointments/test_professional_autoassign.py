"""
Tests de auto-asignación de profesional (appointments/services.py).

El agente de WhatsApp crea citas SIN mandar `professional`. Django debe elegirlo:
ninguna cita puede quedar con `professional=None`, y el contrato del POST no
cambia.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from appointments.models import Appointment, Professional, ProfessionalSchedule, ProfessionalTimeOff
from appointments.services import (
    NoProfessionalAvailable,
    ProfessionalUnavailable,
    SlotUnavailable,
    create_appointment,
    select_professional_for_appointment,
)
from core.models import User


def _next_weekday(target_weekday: int) -> datetime.date:
    today = timezone.localdate()
    return today + timedelta(days=(target_weekday - today.weekday()) % 7 or 7)


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes_10h(clinic_a, madrid):
    """Próximo lunes a las 10:00, hora de la clínica."""
    return datetime.datetime.combine(_next_weekday(0), time(10, 0), tzinfo=madrid)


def _make_professional(clinic, service, email, *, with_schedule=True, **kwargs):
    user = User.objects.create_user(email=email, password='pass', clinic=clinic)
    professional = user.professional_profile
    for field, value in kwargs.items():
        setattr(professional, field, value)
    professional.save()
    professional.services.add(service)
    if with_schedule:
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
    return professional


@pytest.fixture
def prof_1(db, clinic_a, service_a):
    return _make_professional(clinic_a, service_a, 'p1@alpha.test')


@pytest.fixture
def prof_2(db, clinic_a, service_a):
    return _make_professional(clinic_a, service_a, 'p2@alpha.test')


# ---------------------------------------------------------------------------
# Regresión: el payload literal del agente de WhatsApp
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestN8nPayloadRegression:
    def test_n8n_payload_without_professional_creates_appointment(
        self, admin_client, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        """Body exacto del nodo "B - Crear Cita Django". No incluye `professional`.

        Si este test se pone en rojo, el agente está caído en producción.
        """
        payload = {
            'clinic': clinic_a.pk,
            'patient': patient_a.pk,
            'service': service_a.pk,
            'scheduled_at': lunes_10h.isoformat(),
            'end_at': (lunes_10h + timedelta(minutes=30)).isoformat(),
            'status': 'pending',
        }
        response = admin_client.post('/api/appointments/', payload, format='json')

        assert response.status_code == 201
        assert response.data['professional'] == prof_1.pk

        cita = Appointment.objects.get(pk=response.data['id'])
        assert cita.professional_id is not None


# ---------------------------------------------------------------------------
# Auto-asignación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAutoAssign:
    def test_picks_the_only_eligible_professional(
        self, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
            require_online_booking=True, source=Appointment.Source.AGENT,
        )
        assert cita.professional == prof_1

    def test_no_eligible_professional_raises(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        """Sin ningún profesional que preste el servicio → error de dominio."""
        with pytest.raises(NoProfessionalAvailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )
        assert exc.value.detail['code'] == 'no_professional_available'
        assert Appointment.objects.count() == 0

    def test_api_returns_no_professional_available(
        self, admin_client, clinic_a, patient_a, service_a, lunes_10h
    ):
        payload = {
            'clinic': clinic_a.pk,
            'patient': patient_a.pk,
            'service': service_a.pk,
            'scheduled_at': lunes_10h.isoformat(),
            'status': 'pending',
        }
        response = admin_client.post('/api/appointments/', payload, format='json')

        assert response.status_code == 400
        assert response.data['code'] == 'no_professional_available'
        assert 'message' in response.data
        # El mensaje va directo al paciente por WhatsApp: sin IDs ni trazas.
        assert str(clinic_a.pk) not in response.data['message']

    def test_professional_who_does_not_offer_service_is_skipped(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        otro_servicio = service_a.__class__.objects.create(
            clinic=clinic_a, name='Otro', duration_minutes=30, price='10.00', is_active=True,
        )
        _make_professional(clinic_a, otro_servicio, 'nope@alpha.test')

        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_inactive_professional_is_skipped(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        _make_professional(clinic_a, service_a, 'baja@alpha.test', is_active=False)
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_professional_not_accepting_online_booking_is_skipped(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        _make_professional(clinic_a, service_a, 'offline@alpha.test', accepts_online_booking=False)
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_professional_without_schedule_that_day_is_skipped(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        _make_professional(clinic_a, service_a, 'sinhorario@alpha.test', with_schedule=False)
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_professional_on_time_off_is_skipped(
        self, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        ProfessionalTimeOff.objects.create(
            professional=prof_1,
            starts_at=lunes_10h - timedelta(hours=1),
            ends_at=lunes_10h + timedelta(hours=1),
            reason=ProfessionalTimeOff.Reason.VACATION,
        )
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_professional_with_confirmed_overlap_is_skipped(
        self, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        """El hueco está cogido: eso no es "no hay quien la atienda", es otro error."""
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=lunes_10h, end_at=lunes_10h + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        with pytest.raises(SlotUnavailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_professional_with_pending_overlap_is_skipped(
        self, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        """Una 'pending' bloquea igual que una 'confirmed' (ver BLOCKING_STATUSES).

        prof_1 es el único que presta el servicio y ya tiene ese tramo ocupado por
        una cita pendiente de validar → no hay a quien asignársela.
        """
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=lunes_10h, end_at=lunes_10h + timedelta(minutes=30),
            status=Appointment.Status.PENDING,
        )
        with pytest.raises(SlotUnavailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )


# ---------------------------------------------------------------------------
# Desempate: reparto de carga
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTieBreak:
    def test_picks_least_loaded_professional(
        self, clinic_a, patient_a, service_a, prof_1, prof_2, lunes_10h
    ):
        """prof_1 ya tiene una cita bloqueante ese día → se elige prof_2."""
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=lunes_10h + timedelta(hours=2),
            end_at=lunes_10h + timedelta(hours=2, minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        elegido = select_professional_for_appointment(
            clinic=clinic_a, service=service_a,
            scheduled_at=lunes_10h, end_at=lunes_10h + timedelta(minutes=30),
            require_online_booking=True,
        )
        assert elegido == prof_2

    def test_tie_breaks_by_lowest_id(
        self, clinic_a, service_a, prof_1, prof_2, lunes_10h
    ):
        """Misma carga (ninguna) → gana el de menor id, para que sea determinista."""
        assert prof_1.pk < prof_2.pk
        elegido = select_professional_for_appointment(
            clinic=clinic_a, service=service_a,
            scheduled_at=lunes_10h, end_at=lunes_10h + timedelta(minutes=30),
            require_online_booking=True,
        )
        assert elegido == prof_1

    def test_load_is_counted_per_day_not_globally(
        self, clinic_a, patient_a, service_a, prof_1, prof_2, lunes_10h
    ):
        """Una cita de prof_1 en OTRO día no lo penaliza: gana él por id menor."""
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=lunes_10h + timedelta(days=7),
            end_at=lunes_10h + timedelta(days=7, minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        elegido = select_professional_for_appointment(
            clinic=clinic_a, service=service_a,
            scheduled_at=lunes_10h, end_at=lunes_10h + timedelta(minutes=30),
            require_online_booking=True,
        )
        assert elegido == prof_1


# ---------------------------------------------------------------------------
# Profesional explícito: mismas reglas que la auto-asignación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExplicitProfessional:
    def test_valid_explicit_professional_is_respected(
        self, clinic_a, patient_a, service_a, prof_1, prof_2, lunes_10h
    ):
        """Si el cliente elige uno válido, se respeta aunque no sea el que tocaría."""
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=prof_2, scheduled_at=lunes_10h, require_online_booking=True, source=Appointment.Source.AGENT,
        )
        assert cita.professional == prof_2

    def test_explicit_professional_from_other_clinic_rejected(
        self, clinic_a, clinic_b, patient_a, service_a, service_b, lunes_10h
    ):
        ajeno = _make_professional(clinic_b, service_b, 'ajeno@beta.test')
        with pytest.raises(ProfessionalUnavailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=ajeno, scheduled_at=lunes_10h, require_online_booking=True, source=Appointment.Source.AGENT,
            )
        assert exc.value.detail['code'] == 'professional_unavailable'
        assert 'clínica' in exc.value.detail['message']
        assert Appointment.objects.count() == 0

    def test_explicit_professional_not_offering_service_rejected(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        otro_servicio = service_a.__class__.objects.create(
            clinic=clinic_a, name='Otro', duration_minutes=30, price='10.00', is_active=True,
        )
        prof = _make_professional(clinic_a, otro_servicio, 'otro@alpha.test')

        with pytest.raises(ProfessionalUnavailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=prof, scheduled_at=lunes_10h, require_online_booking=True, source=Appointment.Source.AGENT,
            )
        assert 'no ofrece el servicio' in exc.value.detail['message']

    def test_explicit_professional_out_of_schedule_rejected(
        self, clinic_a, patient_a, service_a, prof_1, madrid
    ):
        """Las 20:00 quedan fuera del 09:00–14:00 del profesional."""
        tarde = datetime.datetime.combine(_next_weekday(0), time(20, 0), tzinfo=madrid)
        with pytest.raises(ProfessionalUnavailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=prof_1, scheduled_at=tarde, require_online_booking=True, source=Appointment.Source.AGENT,
            )
        assert 'horario del profesional' in exc.value.detail['message']

    def test_explicit_professional_api_error_shape(
        self, admin_client, clinic_a, clinic_b, patient_a, service_a, service_b, lunes_10h
    ):
        ajeno = _make_professional(clinic_b, service_b, 'ajeno2@beta.test')
        payload = {
            'clinic': clinic_a.pk,
            'patient': patient_a.pk,
            'service': service_a.pk,
            'professional': ajeno.pk,
            'scheduled_at': lunes_10h.isoformat(),
            'status': 'pending',
        }
        response = admin_client.post('/api/appointments/', payload, format='json')
        assert response.status_code == 400
        assert response.data['code'] == 'professional_unavailable'


# ---------------------------------------------------------------------------
# La invariante: ninguna vía crea una cita sin profesional
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInvariant:
    def test_create_appointment_never_leaves_professional_none(
        self, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
            require_online_booking=True, source=Appointment.Source.AGENT,
        )
        assert cita.professional_id is not None

    def test_failure_creates_nothing(
        self, db, clinic_a, patient_a, service_a, lunes_10h
    ):
        """Si no hay profesional, la cita NO se crea a medias."""
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
                require_online_booking=True, source=Appointment.Source.AGENT,
            )
        assert not Appointment.objects.exists()
