"""
Auditoría de la actualización de citas (PATCH /api/appointments/{id}/).

La creación ya garantiza la invariante (ninguna cita nace sin profesional, y el
profesional siempre es elegible). Estos tests comprueban si la ACTUALIZACIÓN
reabre por detrás alguno de esos agujeros.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from appointments.models import Appointment, ProfessionalSchedule, ProfessionalTimeOff
from appointments.services import create_appointment
from core.models import User
from services.models import Service


def _next_weekday(target_weekday: int) -> datetime.date:
    today = timezone.localdate()
    return today + timedelta(days=(target_weekday - today.weekday()) % 7 or 7)


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes_10h(madrid):
    return datetime.datetime.combine(_next_weekday(0), time(10, 0), tzinfo=madrid)


def _make_professional(clinic, service, email, *, with_schedule=True, **kwargs):
    user = User.objects.create_user(email=email, password='pass', clinic=clinic)
    professional = user.professional_profile
    for field, value in kwargs.items():
        setattr(professional, field, value)
    professional.save()
    if service:
        professional.services.add(service)
    if with_schedule:
        ProfessionalSchedule.objects.create(
            professional=professional, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
    return professional


@pytest.fixture
def prof_1(db, clinic_a, service_a):
    return _make_professional(clinic_a, service_a, 'u1@alpha.test')


@pytest.fixture
def prof_2(db, clinic_a, service_a):
    return _make_professional(clinic_a, service_a, 'u2@alpha.test')


@pytest.fixture
def cita(db, clinic_a, patient_a, service_a, prof_1, lunes_10h):
    return create_appointment(
        clinic=clinic_a, patient=patient_a, service=service_a,
        professional=prof_1, scheduled_at=lunes_10h, require_online_booking=True,
    )


@pytest.mark.django_db
class TestPatchLeavesNoOrphanAppointment:
    def test_patch_professional_null_is_rejected(self, admin_client, cita):
        """Una cita viva no puede quedarse sin profesional."""
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/', {'professional': None}, format='json'
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.professional_id is not None


@pytest.mark.django_db
class TestPatchRevalidatesEligibility:
    def test_patch_into_time_off_is_rejected(self, admin_client, cita, prof_1, lunes_10h):
        """Mover la cita a las vacaciones del profesional debe rechazarse."""
        nueva_hora = lunes_10h + timedelta(hours=2)
        ProfessionalTimeOff.objects.create(
            professional=prof_1,
            starts_at=nueva_hora - timedelta(minutes=30),
            ends_at=nueva_hora + timedelta(hours=1),
            reason=ProfessionalTimeOff.Reason.VACATION,
        )
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/',
            {'scheduled_at': nueva_hora.isoformat()},
            format='json',
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.scheduled_at != nueva_hora

    def test_patch_onto_confirmed_appointment_is_rejected(
        self, admin_client, cita, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        """Mover la cita encima de otra CONFIRMADA del mismo profesional."""
        ocupado = lunes_10h + timedelta(hours=2)
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=ocupado, end_at=ocupado + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/',
            {'scheduled_at': ocupado.isoformat()},
            format='json',
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.scheduled_at != ocupado

    def test_patch_outside_schedule_is_rejected(self, admin_client, cita, madrid):
        """Mover la cita a las 20:00, fuera del 09:00–14:00 del profesional."""
        fuera = datetime.datetime.combine(_next_weekday(0), time(20, 0), tzinfo=madrid)
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/',
            {'scheduled_at': fuera.isoformat()},
            format='json',
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.scheduled_at != fuera

    def test_patch_professional_from_other_clinic_is_rejected(
        self, admin_client, cita, clinic_b, service_b
    ):
        ajeno = _make_professional(clinic_b, service_b, 'ajeno@beta.test')
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/', {'professional': ajeno.pk}, format='json'
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.professional_id != ajeno.pk

    def test_patch_professional_not_offering_service_is_rejected(
        self, admin_client, cita, clinic_a
    ):
        otro_servicio = Service.objects.create(
            clinic=clinic_a, name='Otro', duration_minutes=30, price='10.00', is_active=True,
        )
        prof = _make_professional(clinic_a, otro_servicio, 'otro@alpha.test')

        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/', {'professional': prof.pk}, format='json'
        )
        cita.refresh_from_db()

        assert response.status_code == 400
        assert cita.professional_id != prof.pk

    def test_patch_to_valid_professional_is_accepted(self, admin_client, cita, prof_2):
        """El caso legítimo debe seguir funcionando: reasignar a otro válido."""
        response = admin_client.patch(
            f'/api/appointments/{cita.pk}/', {'professional': prof_2.pk}, format='json'
        )
        cita.refresh_from_db()

        assert response.status_code == 200
        assert cita.professional_id == prof_2.pk


@pytest.mark.django_db
class TestBulkUpdateRevalidatesEligibility:
    def test_bulk_update_cannot_orphan_appointment(self, admin_client, cita):
        response = admin_client.patch(
            '/api/appointments/bulk-update/',
            [{'id': str(cita.pk), 'professional': None}],
            format='json',
        )
        cita.refresh_from_db()

        assert cita.professional_id is not None
        assert response.data['errors']

    def test_bulk_update_cannot_move_onto_confirmed(
        self, admin_client, cita, clinic_a, patient_a, service_a, prof_1, lunes_10h
    ):
        ocupado = lunes_10h + timedelta(hours=2)
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof_1,
            scheduled_at=ocupado, end_at=ocupado + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        admin_client.patch(
            '/api/appointments/bulk-update/',
            [{'id': str(cita.pk), 'scheduled_at': ocupado.isoformat()}],
            format='json',
        )
        cita.refresh_from_db()
        assert cita.scheduled_at != ocupado
