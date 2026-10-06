"""Bloques del panel de control que viven en `core.dashboard`.

Las funciones reciben `today`/`now` por parámetro, así que las pruebas fijan el
reloj y no dependen de la hora a la que se ejecuten.
"""
from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ConversationSession
from appointments.models import Appointment, ProfessionalSchedule
from core import dashboard

Status = Appointment.Status


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def noon(today):
    return timezone.make_aware(datetime.combine(today, time(12, 0)))


def _appt(clinic, patient, service, professional, start, minutes=30, status=Status.CONFIRMED, **extra):
    return Appointment.objects.create(
        clinic=clinic, patient=patient, service=service, professional=professional,
        scheduled_at=start, end_at=start + timedelta(minutes=minutes), status=status, **extra,
    )


@pytest.fixture
def base_qs():
    return Appointment.objects.all()


@pytest.mark.django_db
class TestAgenda:
    def test_marca_en_curso_siguiente_y_acciones(
        self, admin_user, clinic_a, patient_a, service_a, professional_a, noon, today, base_qs
    ):
        past = _appt(clinic_a, patient_a, service_a, professional_a, noon - timedelta(hours=3), status=Status.COMPLETED)
        current = _appt(clinic_a, patient_a, service_a, professional_a, noon - timedelta(minutes=10))
        nxt = _appt(clinic_a, patient_a, service_a, professional_a, noon + timedelta(minutes=45), status=Status.PENDING)
        later = _appt(clinic_a, patient_a, service_a, professional_a, noon + timedelta(hours=3))

        rows = {a.pk: a for a in dashboard.today_agenda(admin_user, base_qs, today, noon, None)}

        assert rows[past.pk].is_past
        assert rows[current.pk].is_current and not rows[current.pk].is_next
        assert rows[nxt.pk].is_next and rows[nxt.pk].minutes_until == 45
        assert not rows[later.pk].is_next
        # Lo pendiente se confirma; lo confirmado que ya empezó se completa.
        assert rows[nxt.pk].quick_action[0] == 'confirm'
        assert rows[current.pk].quick_action[0] == 'complete'
        assert rows[later.pk].quick_action is None

    def test_primera_visita_y_chat(
        self, admin_user, clinic_a, patient_a, service_a, professional_a, noon, today, base_qs
    ):
        appt = _appt(clinic_a, patient_a, service_a, professional_a, noon)
        session = ConversationSession.objects.create(phone='600000000', clinic=clinic_a, patient=patient_a)

        row = dashboard.today_agenda(admin_user, base_qs, today, noon, None)[0]
        assert row.is_first_visit and row.chat_session_id == session.id

        _appt(clinic_a, patient_a, service_a, professional_a, noon - timedelta(days=30), status=Status.COMPLETED)
        row = next(a for a in dashboard.today_agenda(admin_user, base_qs, today, noon, None) if a.pk == appt.pk)
        assert not row.is_first_visit


@pytest.mark.django_db
class TestAlertas:
    def test_chat_sin_leer_genera_alerta_y_otra_clinica_no_la_ve(
        self, admin_user, admin_user_b, clinic_a, today, noon, base_qs
    ):
        ConversationSession.objects.create(
            phone='600000001', clinic=clinic_a, unread_count=2, agent_paused=True,
            last_message_at=noon - timedelta(hours=21),
        )
        ConversationSession.objects.create(
            phone='600000002', clinic=clinic_a, unread_count=3, is_test=True, last_message_at=noon,
        )

        keys = {a['key']: a for a in dashboard.dashboard_alerts(admin_user, base_qs, {}, today, noon)}
        assert keys['chats']['count'] == 1  # la de pruebas no cuenta
        assert 'window' in keys and 'human' in keys

        assert dashboard.dashboard_alerts(admin_user_b, base_qs.none(), {}, today, noon) == []

    def test_sin_nada_que_hacer_no_hay_alertas(self, admin_user, today, noon, base_qs):
        assert dashboard.dashboard_alerts(admin_user, base_qs, {}, today, noon) == []


@pytest.mark.django_db
class TestCitasPorEstado:
    def test_reparte_por_estado_y_periodo(
        self, clinic_a, patient_a, service_a, professional_a, noon, today, base_qs
    ):
        _appt(clinic_a, patient_a, service_a, professional_a, noon, status=Status.PENDING)
        _appt(clinic_a, patient_a, service_a, professional_a, noon + timedelta(hours=1), status=Status.CANCELLED)
        # Fuera de «hoy», dentro de «mes» salvo que caiga en otro mes.
        _appt(clinic_a, patient_a, service_a, professional_a, noon + timedelta(days=40), status=Status.PENDING)

        card = dashboard.appointments_by_status(base_qs, 'hoy', today)
        counts = {s['status']: s['count'] for s in card['segments']}
        assert card['total'] == 2 and counts['pending'] == 1 and counts['cancelled'] == 1
        assert sum(s['pct'] for s in card['segments']) in (99, 100, 101)

        assert dashboard.appointments_by_status(base_qs, 'inventado', today)['period'] == 'hoy'

    def test_htmx_devuelve_solo_la_tarjeta(self, client, admin_user):
        client.force_login(admin_user)
        url = reverse('core:dashboard')

        fragment = client.get(url, {'bloque': 'citas', 'periodo': 'semana'}, HTTP_HX_REQUEST='true').content.decode()
        full = client.get(url, {'periodo': 'semana'}).content.decode()

        assert 'id="citas-estado"' in fragment and '<html' not in fragment
        assert 'id="citas-estado"' in full and '<html' in full


@pytest.mark.django_db
class TestOcupacion:
    def test_minutos_reservados_sobre_horario(
        self, clinic_a, patient_a, service_a, professional_a, today, base_qs
    ):
        monday = today - timedelta(days=today.weekday())
        ProfessionalSchedule.objects.filter(professional=professional_a).delete()
        ProfessionalSchedule.objects.create(
            professional=professional_a, day_of_week=0, start_time=time(9), end_time=time(13),
        )
        start = timezone.make_aware(datetime.combine(monday, time(9)))
        _appt(clinic_a, patient_a, service_a, professional_a, start, minutes=60)

        data = dashboard.week_occupancy([professional_a], base_qs, today)

        assert data['offered_h'] == 4.0 and data['booked_h'] == 1.0 and data['pct'] == 25

    def test_sin_horarios_no_hay_porcentaje(self, professional_a, today, base_qs):
        ProfessionalSchedule.objects.filter(professional=professional_a).delete()
        assert dashboard.week_occupancy([professional_a], base_qs, today)['pct'] is None


@pytest.mark.django_db
class TestRecontacto:
    def test_ultima_visita_antigua_y_sin_cita_futura(
        self, admin_user, clinic_a, patient_a, service_a, professional_a, noon
    ):
        old = _appt(clinic_a, patient_a, service_a, professional_a, noon - timedelta(days=300), status=Status.COMPLETED)
        assert dashboard.patients_to_recontact(admin_user, noon)['patients'] == [patient_a]

        _appt(clinic_a, patient_a, service_a, professional_a, noon + timedelta(days=5))
        assert dashboard.patients_to_recontact(admin_user, noon)['total'] == 0
        assert old.pk  # la visita antigua sigue existiendo


@pytest.mark.django_db
class TestPanelCompleto:
    def test_renderiza_con_datos(
        self, client, admin_user, clinic_a, patient_a, service_a, professional_a, noon
    ):
        _appt(clinic_a, patient_a, service_a, professional_a, timezone.now(), status=Status.PENDING)
        ConversationSession.objects.create(
            phone='600000003', clinic=clinic_a, patient=patient_a, unread_count=1, last_message_at=timezone.now(),
        )
        client.force_login(admin_user)

        response = client.get(reverse('core:dashboard'))

        html = response.content.decode()
        assert response.status_code == 200
        assert 'Requiere atención' in html and 'Agenda de hoy' in html
        assert 'Próximos 7 días' in html and 'Ocupación de la semana' in html
