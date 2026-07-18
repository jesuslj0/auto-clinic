"""Fase 1 del diseño A: la confirmación del paciente y la de la clínica son cosas distintas.

`confirmed` significa UNA cosa: "la clínica tiene la cita en firme". Que el
paciente responda "SÍ" al recordatorio es un hecho ortogonal, y se guarda como
tal (`patient_confirmed_at`), sin tocar el estado. La asimetría con el "NO" (que
sí cancela) es intencionada: cancelar libera un hueco, confirmar asistencia no
mueve ningún recurso.
"""
import datetime
from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import (
    Appointment,
    AppointmentStatusHistory,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)
from appointments.services import (
    InvalidTransition,
    confirm_by_clinic,
    create_appointment,
    register_patient_confirmation,
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
def cita_agente(clinic_a, patient_a, service_a, prof, lunes_10h):
    """Una cita como la que crea el agente de WhatsApp: pendiente de validar."""
    return create_appointment(
        clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
        require_online_booking=True, source=Appointment.Source.AGENT,
    )


# ---------------------------------------------------------------------------
# El paciente responde al recordatorio
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPatientAnswer:
    def test_yes_records_the_fact_and_leaves_status_untouched(self, api_client, cita_agente):
        response = api_client.post(
            f'/api/public/appointments/{cita_agente.confirmation_token}/confirm/'
        )
        assert response.status_code == 200

        cita_agente.refresh_from_db()
        assert cita_agente.patient_confirmed_at is not None
        assert cita_agente.status == Appointment.Status.PENDING  # intacto
        assert cita_agente.reminder_responded is True

    def test_yes_does_not_write_a_status_transition(self, cita_agente):
        """No hay transición que registrar: nada se ocupó ni se liberó."""
        antes = cita_agente.status_history.count()
        register_patient_confirmation(cita_agente)
        assert cita_agente.status_history.count() == antes

    def test_yes_is_idempotent(self, cita_agente):
        register_patient_confirmation(cita_agente)
        primera_vez = cita_agente.patient_confirmed_at

        register_patient_confirmation(cita_agente)
        cita_agente.refresh_from_db()
        assert cita_agente.patient_confirmed_at == primera_vez

    def test_yes_on_a_cancelled_appointment_is_rejected(self, cita_agente):
        cita_agente.status = Appointment.Status.CANCELLED
        cita_agente.save(update_fields=['status'])
        with pytest.raises(InvalidTransition):
            register_patient_confirmation(cita_agente)

    def test_no_cancels_because_it_frees_the_slot(self, api_client, cita_agente):
        response = api_client.post(
            f'/api/public/appointments/{cita_agente.confirmation_token}/cancel/'
        )
        assert response.status_code == 200

        cita_agente.refresh_from_db()
        assert cita_agente.status == Appointment.Status.CANCELLED
        assert cita_agente.cancelled_by == Appointment.CancelledBy.PATIENT
        # Esto SÍ es una transición: queda en el historial.
        assert cita_agente.status_history.filter(
            to_status=Appointment.Status.CANCELLED,
            actor=AppointmentStatusHistory.Actor.PATIENT,
        ).exists()


# ---------------------------------------------------------------------------
# La clínica valida la cita
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestClinicConfirmation:
    def test_staff_endpoint_confirms_and_clears_the_hold(self, admin_client, cita_agente):
        assert cita_agente.hold_expires_at is not None

        response = admin_client.post(f'/api/appointments/{cita_agente.pk}/confirm/')
        assert response.status_code == 200

        cita_agente.refresh_from_db()
        assert cita_agente.status == Appointment.Status.CONFIRMED
        assert cita_agente.hold_expires_at is None  # una cita en firme no caduca

    def test_confirmation_is_recorded_in_the_history(self, cita_agente, admin_user):
        confirm_by_clinic(cita_agente, user=admin_user)
        transicion = cita_agente.status_history.latest('changed_at')
        assert transicion.from_status == Appointment.Status.PENDING
        assert transicion.to_status == Appointment.Status.CONFIRMED
        assert transicion.actor == AppointmentStatusHistory.Actor.STAFF

    def test_agent_key_cannot_confirm(self, agent_client, cita_agente):
        """Confirmar es de la clínica: la API key del agente no basta."""
        response = agent_client.post(f'/api/appointments/{cita_agente.pk}/confirm/')
        assert response.status_code == 403

        cita_agente.refresh_from_db()
        assert cita_agente.status == Appointment.Status.PENDING

    def test_agent_cannot_confirm_via_patch_status(self, agent_client, cita_agente):
        """La puerta de atrás también está cerrada: escribir status=confirmed por
        el PATCH general se rechaza. Confirmar es solo del staff."""
        response = agent_client.patch(
            f'/api/appointments/{cita_agente.pk}/', {'status': 'confirmed'}, format='json',
        )
        assert response.status_code == 400

        cita_agente.refresh_from_db()
        assert cita_agente.status == Appointment.Status.PENDING

    def test_cancelled_appointment_cannot_be_confirmed(self, cita_agente):
        cita_agente.status = Appointment.Status.CANCELLED
        cita_agente.save(update_fields=['status'])
        with pytest.raises(InvalidTransition):
            confirm_by_clinic(cita_agente)

    def test_confirming_twice_is_idempotent(self, cita_agente, admin_user):
        confirm_by_clinic(cita_agente, user=admin_user)
        confirm_by_clinic(cita_agente, user=admin_user)
        assert cita_agente.status_history.filter(
            to_status=Appointment.Status.CONFIRMED
        ).count() == 1

    def test_panel_action_confirms_through_the_service(self, client, admin_user, cita_agente):
        """El panel no escribe el estado a mano: usa el mismo service."""
        client.force_login(admin_user)
        response = client.post(
            reverse('core:dashboard-appointment-action', args=[cita_agente.pk]),
            {'action': 'confirm'},
        )
        assert response.status_code == 302

        cita_agente.refresh_from_db()
        assert cita_agente.status == Appointment.Status.CONFIRMED
        assert cita_agente.hold_expires_at is None


# ---------------------------------------------------------------------------
# Dónde nace cada cita, y con qué hold
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSourceAndHold:
    def test_agent_appointment_is_born_pending_with_a_hold(self, cita_agente, clinic_a):
        assert cita_agente.source == Appointment.Source.AGENT
        assert cita_agente.status == Appointment.Status.PENDING

        esperado = timezone.now() + timedelta(minutes=clinic_a.hold_ttl_minutes)
        assert abs((cita_agente.hold_expires_at - esperado).total_seconds()) < 60

    def test_staff_appointment_is_born_confirmed_without_a_hold(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        """La pone la clínica: ya está en firme, no hay nada que validar después."""
        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
            require_online_booking=False, source=Appointment.Source.STAFF,
        )
        assert cita.status == Appointment.Status.CONFIRMED
        assert cita.hold_expires_at is None

    def test_panel_form_creates_a_confirmed_appointment(
        self, client, admin_user, patient_a, service_a, prof, lunes
    ):
        client.force_login(admin_user)
        response = client.post(
            reverse('appointments:create'),
            {
                'patient': patient_a.pk, 'service': service_a.pk, 'professional': prof.pk,
                'date': lunes.isoformat(), 'time': '10:00', 'notes': '',
            },
        )
        assert response.status_code == 302

        cita = Appointment.objects.get()
        assert cita.source == Appointment.Source.STAFF
        assert cita.status == Appointment.Status.CONFIRMED
        assert cita.hold_expires_at is None

    def test_zero_ttl_means_no_expiry(
        self, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        clinic_a.hold_ttl_minutes = 0
        clinic_a.save(update_fields=['hold_ttl_minutes'])

        cita = create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, scheduled_at=lunes_10h,
            require_online_booking=True, source=Appointment.Source.AGENT,
        )
        assert cita.status == Appointment.Status.PENDING
        assert cita.hold_expires_at is None

    def test_source_is_not_part_of_the_api_contract(
        self, admin_client, clinic_a, patient_a, service_a, prof, lunes_10h
    ):
        """Los tres campos nuevos son estado interno: n8n no los ve ni los escribe."""
        payload = {
            'clinic': clinic_a.pk,
            'patient': patient_a.pk,
            'service': service_a.pk,
            'scheduled_at': lunes_10h.isoformat(),
            'status': 'pending',
            # Un cliente malicioso intentando eximirse de la caducidad:
            'hold_expires_at': None,
            'source': 'staff',
        }
        response = admin_client.post('/api/appointments/', payload, format='json')
        assert response.status_code == 201
        assert 'source' not in response.data
        assert 'hold_expires_at' not in response.data
        assert 'patient_confirmed_at' not in response.data

        cita = Appointment.objects.get(pk=response.data['id'])
        assert cita.source == Appointment.Source.AGENT  # lo fija el servidor
        assert cita.hold_expires_at is not None  # el intento se ignoró


# ---------------------------------------------------------------------------
# pending-reminders
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPendingReminders:
    @pytest.fixture
    def cita_manana(self, clinic_a, patient_a, service_a, professional_a):
        """Una cita dentro de la ventana de las 24h."""
        cuando = timezone.now() + timedelta(hours=24)
        return Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=professional_a, scheduled_at=cuando,
            end_at=cuando + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

    def _ids(self, response):
        return {row['id'] for row in response.data['results']}

    def test_confirmed_appointment_gets_a_reminder(self, admin_client, cita_manana):
        """El bug que esto arregla: hoy una cita validada por el staff se quedaba
        sin recordatorio si el flag `reminder_responded` hacía de estado."""
        response = admin_client.get('/api/appointments/pending-reminders/?type=24h')
        assert response.status_code == 200
        assert str(cita_manana.pk) in self._ids(response)
        assert response.data['count'] == 1

    def test_pending_appointment_gets_no_reminder(self, admin_client, cita_manana):
        """Aún no la ha validado la clínica: no le pedimos al paciente que confirme."""
        cita_manana.status = Appointment.Status.PENDING
        cita_manana.save(update_fields=['status'])

        response = admin_client.get('/api/appointments/pending-reminders/?type=24h')
        assert response.data['count'] == 0

    def test_patient_who_already_answered_is_not_reminded_again(
        self, admin_client, cita_manana
    ):
        register_patient_confirmation(cita_manana)
        response = admin_client.get('/api/appointments/pending-reminders/?type=24h')
        assert response.data['count'] == 0

    def test_response_shape_is_unchanged(self, admin_client, cita_manana):
        """n8n consume esto: {results, count} y nada más."""
        response = admin_client.get('/api/appointments/pending-reminders/?type=24h')
        assert set(response.data.keys()) == {'results', 'count'}

    def test_marking_reminder_sent_does_not_revalidate_eligibility(
        self, admin_client, cita_manana
    ):
        """n8n marca `reminder_24h_sent` en cada envío. Ese PATCH no mueve el hueco,
        así que no debe fallar aunque el profesional dejara de ser elegible
        entretanto (aquí, una ausencia sobrevenida que solapa la cita)."""
        ProfessionalTimeOff.objects.create(
            professional=cita_manana.professional,
            starts_at=cita_manana.scheduled_at - timedelta(minutes=30),
            ends_at=cita_manana.scheduled_at + timedelta(minutes=30),
            reason=ProfessionalTimeOff.Reason.SICK_LEAVE,
        )
        response = admin_client.patch(
            f'/api/appointments/{cita_manana.pk}/',
            {'reminder_24h_sent': True}, format='json',
        )
        assert response.status_code == 200
        cita_manana.refresh_from_db()
        assert cita_manana.reminder_24h_sent is True
