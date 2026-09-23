"""Reprogramación: el agente mueve la hora de una cita y la cita queda señalada.

Mover una cita no la sustituye por otra: conserva su id y su
`confirmation_token`, así que el enlace que el paciente ya tiene le sigue
valiendo. Lo que cambia es que pasa a `rescheduled`, un estado VIVO y que
BLOQUEA —no una lápida—, y que la clínica lo ve en la agenda hasta que valida la
hora nueva.

El estado no se escribe a mano por la API: es la consecuencia de mover la hora.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from appointments.models import (
    BLOCKING_STATUSES,
    LIVE_STATUSES,
    Appointment,
    AppointmentStatusHistory,
    ProfessionalSchedule,
)
from appointments.services import (
    InvalidTransition,
    SlotUnavailable,
    cancel_appointment,
    confirm_by_clinic,
    create_appointment,
    reschedule_appointment,
)
from core.models import User


def _next_weekday(target_weekday: int) -> datetime.date:
    today = timezone.localdate()
    return today + timedelta(days=(target_weekday - today.weekday()) % 7 or 7)


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes_10h(madrid):
    return datetime.datetime.combine(_next_weekday(0), time(10, 0), tzinfo=madrid)


@pytest.fixture
def lunes_12h(madrid):
    return datetime.datetime.combine(_next_weekday(0), time(12, 0), tzinfo=madrid)


@pytest.fixture
def prof(db, clinic_a, service_a):
    user = User.objects.create_user(email='prof@alpha.test', password='pass', clinic=clinic_a)
    professional = user.professional_profile
    professional.services.add(service_a)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0, start_time=time(9, 0), end_time=time(14, 0),
    )
    return professional


@pytest.fixture
def agent_client(api_client, clinic_a):
    """APIClient autenticado con la Api-Key de la clínica, como n8n."""
    api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
    return api_client


@pytest.fixture
def cita(clinic_a, patient_a, service_a, prof, lunes_10h):
    return create_appointment(
        clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
        require_online_booking=True, source=Appointment.Source.AGENT,
    )


# ---------------------------------------------------------------------------
# El estado nuevo no rompe las dos reglas que lo rodean
# ---------------------------------------------------------------------------


def test_rescheduled_is_alive_and_blocks():
    """La regresión que más caro sale: si `rescheduled` se cae de estos conjuntos,
    la cita movida deja de ocupar su hueco Y el PATCH que la mueve deja de mirar
    si el destino está libre (`validate_appointment_update` corta antes)."""
    assert Appointment.Status.RESCHEDULED in LIVE_STATUSES
    assert Appointment.Status.RESCHEDULED in BLOCKING_STATUSES


@pytest.mark.django_db
def test_a_rescheduled_appointment_still_owns_its_new_slot(
    clinic_a, patient_a, service_a, prof, cita, lunes_12h
):
    reschedule_appointment(cita, scheduled_at=lunes_12h)

    with pytest.raises(SlotUnavailable):
        create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_12h,
            professional=prof, require_online_booking=True, source=Appointment.Source.AGENT,
        )


@pytest.mark.django_db
def test_it_cannot_be_moved_onto_an_occupied_slot(
    clinic_a, patient_a, service_a, prof, cita, lunes_12h
):
    create_appointment(
        clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_12h,
        professional=prof, require_online_booking=True, source=Appointment.Source.AGENT,
    )

    with pytest.raises(SlotUnavailable):
        reschedule_appointment(cita, scheduled_at=lunes_12h)

    cita.refresh_from_db()
    assert cita.status == Appointment.Status.PENDING


# ---------------------------------------------------------------------------
# La reprogramación en sí
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_agent_moves_the_hour_by_patch(agent_client, cita, lunes_12h):
    response = agent_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'scheduled_at': lunes_12h.isoformat()},
        format='json',
    )

    assert response.status_code == 200, response.data
    cita.refresh_from_db()
    assert cita.scheduled_at == lunes_12h
    assert cita.status == Appointment.Status.RESCHEDULED
    # `end_at` se recalcula desde la duración del servicio: arrastrar el viejo
    # dejaría la cita ocupando un tramo que no es el suyo.
    assert cita.end_at == lunes_12h + timedelta(
        minutes=cita.service.booking_duration_minutes
    )


@pytest.mark.django_db
def test_the_move_keeps_the_patient_link_working(agent_client, cita, lunes_12h):
    """Se mueve la cita, no se sustituye: el enlace que el paciente ya tiene vale."""
    token_previo = cita.confirmation_token

    agent_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'scheduled_at': lunes_12h.isoformat()},
        format='json',
    )

    cita.refresh_from_db()
    assert cita.confirmation_token == token_previo


@pytest.mark.django_db
def test_the_move_is_recorded_in_the_history(cita, lunes_12h):
    reschedule_appointment(
        cita, scheduled_at=lunes_12h, actor=AppointmentStatusHistory.Actor.AGENT
    )

    entrada = cita.status_history.latest('changed_at')
    assert entrada.from_status == Appointment.Status.PENDING
    assert entrada.to_status == Appointment.Status.RESCHEDULED
    assert entrada.actor == AppointmentStatusHistory.Actor.AGENT


@pytest.mark.django_db
def test_the_api_records_the_agent_as_the_actor(agent_client, cita, lunes_12h):
    agent_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'scheduled_at': lunes_12h.isoformat()},
        format='json',
    )

    assert cita.status_history.latest('changed_at').actor == AppointmentStatusHistory.Actor.AGENT


@pytest.mark.django_db
def test_a_confirmed_appointment_does_not_go_back_to_pending(
    admin_user, agent_client, cita, lunes_12h
):
    """Moverla no la degrada: seguía en firme y no vuelve a estar a prueba."""
    confirm_by_clinic(cita, user=admin_user)

    agent_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'scheduled_at': lunes_12h.isoformat()},
        format='json',
    )

    cita.refresh_from_db()
    assert cita.status == Appointment.Status.RESCHEDULED
    # Sin hold: una cita que ya estaba validada no puede caducar por haberla movido.
    assert cita.hold_expires_at is None


@pytest.mark.django_db
def test_moving_it_resets_everything_known_about_the_old_hour(
    admin_user, cita, lunes_12h
):
    """El recordatorio ya enviado y el "sí" del paciente eran para la hora vieja.

    Si el flag de 24h sobreviviera, una cita movida a la semana que viene no
    volvería a avisar al paciente nunca: `pending-reminders` filtra por
    `reminder_24h_sent=False`.
    """
    confirm_by_clinic(cita, user=admin_user)
    Appointment.objects.filter(pk=cita.pk).update(
        reminder_24h_sent=True,
        reminder_24h_sent_at=timezone.now(),
        reminder_responded=True,
        patient_confirmed_at=timezone.now(),
    )
    cita.refresh_from_db()

    reschedule_appointment(cita, scheduled_at=lunes_12h)

    cita.refresh_from_db()
    assert cita.reminder_24h_sent is False
    assert cita.reminder_24h_sent_at is None
    assert cita.reminder_3h_sent is False
    assert cita.reminder_responded is False
    assert cita.patient_confirmed_at is None


@pytest.mark.django_db
def test_a_cancelled_appointment_cannot_be_rescheduled(cita, lunes_12h):
    cancel_appointment(cita)

    with pytest.raises(InvalidTransition):
        reschedule_appointment(cita, scheduled_at=lunes_12h)


# ---------------------------------------------------------------------------
# El estado es una consecuencia, no una etiqueta que se pega
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_status_cannot_be_written_by_hand(agent_client, cita):
    response = agent_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'status': Appointment.Status.RESCHEDULED},
        format='json',
    )

    assert response.status_code == 400
    assert 'status' in response.data
    cita.refresh_from_db()
    assert cita.status == Appointment.Status.PENDING


@pytest.mark.django_db
def test_the_staff_moving_an_appointment_does_not_mark_it(admin_client, cita, lunes_12h):
    """La marca existe para avisar a la clínica de lo que hizo el agente. Si la
    mueve la propia clínica no hay nada que avisar."""
    response = admin_client.patch(
        f'/api/appointments/{cita.pk}/',
        {'scheduled_at': lunes_12h.isoformat()},
        format='json',
    )

    assert response.status_code == 200, response.data
    cita.refresh_from_db()
    assert cita.scheduled_at == lunes_12h
    assert cita.status == Appointment.Status.PENDING


# ---------------------------------------------------------------------------
# Salir de `rescheduled`
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_clinic_validates_the_new_hour(admin_user, cita, lunes_12h):
    """`rescheduled` no es terminal: validarla es cómo se sale, y es el gesto con
    el que la clínica da por buena la hora que puso el agente."""
    reschedule_appointment(cita, scheduled_at=lunes_12h)

    confirm_by_clinic(cita, user=admin_user)

    cita.refresh_from_db()
    assert cita.status == Appointment.Status.CONFIRMED
    assert cita.hold_expires_at is None
    entrada = cita.status_history.latest('changed_at')
    assert entrada.from_status == Appointment.Status.RESCHEDULED
    assert entrada.to_status == Appointment.Status.CONFIRMED


@pytest.mark.django_db
def test_the_patient_can_still_cancel_it(cita, lunes_12h):
    """Sigue viva, así que la vía pública del paciente la alcanza igual."""
    reschedule_appointment(cita, scheduled_at=lunes_12h)

    cancel_appointment(cita, cancelled_by=Appointment.CancelledBy.PATIENT)

    cita.refresh_from_db()
    assert cita.status == Appointment.Status.CANCELLED
