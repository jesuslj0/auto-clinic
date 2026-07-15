"""
`accepts_online_booking` solo aplica a la vía online.

El campo significa "el agente / la reserva pública pueden ofrecer a este
profesional", no "está de baja" (para eso está `is_active`). El staff sí puede
asignarle citas desde el panel y el admin.

La distinción va atada a QUIÉN LLAMA, no a si `professional` venía en el payload:

| Origen        | ¿professional explícito? | ¿aplica accepts_online_booking? |
|---------------|--------------------------|---------------------------------|
| API / agente  | no (auto-asigna)         | sí                              |
| API / agente  | sí                       | sí                              |
| Panel / admin | sí                       | no                              |
| Panel / admin | no (auto-asigna)         | no                              |
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, ProfessionalSchedule
from appointments.services import (
    NoProfessionalAvailable,
    ProfessionalUnavailable,
    SlotUnavailable,
    create_appointment,
)
from core.models import User


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


def _make_professional(clinic, service, email, **kwargs):
    user = User.objects.create_user(email=email, password='pass', clinic=clinic)
    professional = user.professional_profile
    for field, value in kwargs.items():
        setattr(professional, field, value)
    professional.save()
    professional.services.add(service)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0,
        start_time=time(9, 0), end_time=time(14, 0),
    )
    return professional


@pytest.fixture
def offline_prof(db, clinic_a, service_a):
    """Solo acepta citas por teléfono: el agente no debe ofrecerlo, el staff sí."""
    return _make_professional(
        clinic_a, service_a, 'telefono@alpha.test', accepts_online_booking=False
    )


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


# ---------------------------------------------------------------------------
# Vía online (API / agente): el flag SÍ se aplica
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnlineVia:
    def test_autoassign_skips_offline_professional(
        self, clinic_a, patient_a, service_a, offline_prof, lunes_10h
    ):
        """Es el único elegible, pero no acepta reservas online → no hay hueco."""
        with pytest.raises(NoProfessionalAvailable):
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                scheduled_at=lunes_10h, require_online_booking=True, source=Appointment.Source.AGENT,
            )

    def test_explicit_offline_professional_is_rejected(
        self, admin_client, clinic_a, patient_a, service_a, offline_prof, lunes_10h
    ):
        """Aunque venga explícito en el payload, sigue siendo una reserva online."""
        payload = {
            'clinic': clinic_a.pk,
            'patient': patient_a.pk,
            'service': service_a.pk,
            'professional': offline_prof.pk,
            'scheduled_at': lunes_10h.isoformat(),
            'status': 'pending',
        }
        response = admin_client.post('/api/appointments/', payload, format='json')

        assert response.status_code == 400
        assert response.data['code'] == 'professional_unavailable'
        assert 'reservas online' in response.data['message']

    def test_api_autoassign_returns_no_professional_available(
        self, admin_client, clinic_a, patient_a, service_a, offline_prof, lunes_10h
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


# ---------------------------------------------------------------------------
# Vía staff (panel / admin): el flag NO se aplica
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffVia:
    def test_panel_form_accepts_offline_professional(
        self, panel_client, clinic_a, patient_a, service_a, offline_prof, lunes
    ):
        """El staff sí puede darle cita desde el panel."""
        response = panel_client.post(
            reverse('appointments:create'),
            {
                'patient': patient_a.pk,
                'service': service_a.pk,
                'professional': offline_prof.pk,
                'date': lunes.isoformat(),
                'time': '10:00',
                'notes': '',
            },
        )

        assert response.status_code == 302  # redirige al calendario: se creó
        cita = Appointment.objects.get()
        assert cita.professional == offline_prof

    def test_service_call_from_staff_accepts_offline_professional(
        self, clinic_a, patient_a, service_a, offline_prof, lunes_10h
    ):
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=offline_prof, scheduled_at=lunes_10h,
            require_online_booking=False, source=Appointment.Source.STAFF,
        )
        assert cita.professional == offline_prof

    def test_staff_autoassign_can_pick_offline_professional(
        self, clinic_a, patient_a, service_a, offline_prof, lunes_10h
    ):
        """Sin profesional explícito por vía staff, el flag tampoco descarta."""
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a,
            scheduled_at=lunes_10h, require_online_booking=False, source=Appointment.Source.STAFF,
        )
        assert cita.professional == offline_prof

    def test_admin_accepts_offline_professional(
        self, admin_site_client, clinic_a, patient_a, service_a, offline_prof, lunes
    ):
        # El admin interpreta la fecha/hora en el TIME_ZONE de Django, no en UTC.
        response = admin_site_client.post(
            '/admin/appointments/appointment/add/',
            {
                'clinic': clinic_a.pk,
                'patient': patient_a.pk,
                'service': service_a.pk,
                'professional': offline_prof.pk,
                'scheduled_at_0': lunes.isoformat(),
                'scheduled_at_1': '10:00:00',
                'status': 'pending',
                'cancellation_policy_hours': 24,
                'patient_phone': '',
                'patient_name': '',
                'service_name': '',
                'external_id': '',
                'external_calendar_id': '',
                'notes': '',
                'status_history-TOTAL_FORMS': '0',
                'status_history-INITIAL_FORMS': '0',
            },
        )
        assert response.status_code == 302
        assert Appointment.objects.get().professional == offline_prof


# ---------------------------------------------------------------------------
# Solo se relaja `accepts_online_booking`: nada más
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOnlyThatFlagIsRelaxed:
    def test_inactive_professional_rejected_from_panel_too(
        self, panel_client, clinic_a, patient_a, service_a, lunes
    ):
        """`is_active=False` se rechaza también por vía staff."""
        baja = _make_professional(clinic_a, service_a, 'baja@alpha.test', is_active=False)
        response = panel_client.post(
            reverse('appointments:create'),
            {
                'patient': patient_a.pk,
                'service': service_a.pk,
                'professional': baja.pk,
                'date': lunes.isoformat(),
                'time': '10:00',
                'notes': '',
            },
        )
        # No redirige: el form se repinta con el error.
        assert response.status_code == 200
        assert not Appointment.objects.exists()

    def test_staff_cannot_book_over_confirmed_appointment(
        self, clinic_a, patient_a, service_a, offline_prof, lunes_10h
    ):
        """El solapamiento se sigue validando por vía staff."""
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=offline_prof, scheduled_at=lunes_10h,
            end_at=lunes_10h + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )
        with pytest.raises(SlotUnavailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=offline_prof, scheduled_at=lunes_10h,
                require_online_booking=False, source=Appointment.Source.STAFF,
            )
        assert exc.value.detail['code'] == 'slot_unavailable'


    def test_staff_cannot_book_outside_schedule(
        self, clinic_a, patient_a, service_a, offline_prof, lunes, madrid
    ):
        """El horario se sigue validando por vía staff."""
        fuera = datetime.datetime.combine(lunes, time(20, 0), tzinfo=madrid)
        with pytest.raises(ProfessionalUnavailable) as exc:
            create_appointment(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=offline_prof, scheduled_at=fuera,
                require_online_booking=False, source=Appointment.Source.STAFF,
            )
        assert 'horario del profesional' in exc.value.detail['message']
