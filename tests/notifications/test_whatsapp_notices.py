"""Avisos de cita por WhatsApp: confirmada, cambiada y cancelada, desde Django con plantilla.

Cubre las tres promesas del paso 3.1 del plan: solo avisa cuando el cambio lo hace
la clínica (no duplica lo que el agente ya contestó), no sale sin plantilla ni sin
teléfono, y no falla en silencio ni se repite.
"""
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, WhatsAppTemplate
from agent.whatsapp import WhatsAppError
from appointments.models import Appointment, AppointmentStatusHistory, ProfessionalSchedule
from appointments.services import (
    cancel_appointment,
    confirm_by_clinic,
    create_appointment,
    reschedule_appointment,
)
from notifications.whatsapp_notices import MAX_ATTEMPTS, NOTICES, send_appointment_notice, send_notice

Status = WhatsAppTemplate.Status
Purpose = WhatsAppTemplate.Purpose
Actor = AppointmentStatusHistory.Actor

PHONE = '+34600111222'

#: Qué dato va en cada variable de cada plantilla de prueba.
TEMPLATES = {
    'booking_confirmed': (
        'Hola {{1}}, tu cita en {{2}} del {{3}} a las {{4}} está confirmada.',
        {'1': 'patient_first_name', '2': 'clinic_name', '3': 'date', '4': 'time'},
    ),
    'booking_changed': (
        'Hola {{1}}, tu cita del {{2}} a las {{3}} pasa al {{4}} a las {{5}}.',
        {'1': 'patient_first_name', '2': 'previous_date', '3': 'previous_time', '4': 'date', '5': 'time'},
    ),
    'booking_cancelled': (
        'Hola {{1}}, hemos cancelado tu cita del {{2}} a las {{3}}.',
        {'1': 'patient_first_name', '2': 'date', '3': 'time'},
    ),
}


@pytest.fixture
def connected(clinic_a):
    clinic_a.whatsapp_phone_number_id = '111'
    clinic_a.whatsapp_token = 'EAAG'
    clinic_a.save()
    return clinic_a


def _template(clinic, kind, **overrides):
    body, variable_map = TEMPLATES[kind]
    fields = dict(
        clinic=clinic, name=f'aviso_{kind}', body=body, status=Status.APPROVED, meta_template_id='9',
        body_examples=['x'] * len(variable_map), purpose=NOTICES[kind][0], variable_map=variable_map,
    )
    return WhatsAppTemplate.objects.create(**{**fields, **overrides})


@pytest.fixture
def templates(connected):
    return {kind: _template(connected, kind) for kind in TEMPLATES}


@pytest.fixture
def appointment(clinic_a, patient_a, service_a, professional_a):
    patient_a.phone = PHONE
    patient_a.save()
    when = timezone.now() + timedelta(days=3)
    return Appointment.objects.create(
        clinic=clinic_a, patient=patient_a, service=service_a, professional=professional_a,
        scheduled_at=when, end_at=when + timedelta(minutes=30), status=Appointment.Status.CONFIRMED,
    )


@pytest.fixture
def meta():
    # Un wamid distinto por envío, como hace Meta: es único en `chat_messages`.
    with patch('agent.services.send_template', side_effect=lambda *a: f'wamid.{uuid.uuid4().hex}') as send:
        yield send


def _send(appointment, kind, *, event=None, previous_at=None, scheduled_at=None):
    return send_notice(
        appointment.pk, kind, event or uuid.uuid4().hex,
        (scheduled_at or appointment.scheduled_at).isoformat(),
        previous_at.isoformat() if previous_at else None,
    )


def _local(when, clinic):
    return when.astimezone(ZoneInfo(clinic.timezone))


@pytest.mark.django_db
class TestSending:
    @pytest.mark.parametrize('kind', ['booking_confirmed', 'booking_cancelled'])
    def test_sends_the_clinics_template_with_the_appointments_data(self, appointment, templates, meta, kind):
        if kind == 'booking_cancelled':
            appointment.status = Appointment.Status.CANCELLED
            appointment.save()

        assert _send(appointment, kind) == 'sent'

        clinic, phone, name, language, params = meta.call_args.args
        assert (phone, name, language) == (PHONE, f'aviso_{kind}', 'es')
        assert params[0] == 'John'
        assert _local(appointment.scheduled_at, appointment.clinic).strftime('%H:%M') in params

    def test_the_change_notice_carries_the_previous_date_and_time(self, appointment, templates, meta):
        before = appointment.scheduled_at - timedelta(days=2, hours=1)

        assert _send(appointment, 'booking_changed', previous_at=before) == 'sent'

        params = meta.call_args.args[4]
        clinic = appointment.clinic
        assert params[2] == _local(before, clinic).strftime('%H:%M')
        assert params[4] == _local(appointment.scheduled_at, clinic).strftime('%H:%M')
        assert params[1] != params[3]  # fecha anterior y fecha nueva

    def test_it_is_left_in_the_thread_without_pausing_the_agent(self, appointment, templates, meta):
        _send(appointment, 'booking_confirmed', event='ev1')

        message = ChatMessage.objects.get(message_type='template')
        assert message.status == ChatMessage.Status.SENT
        # Un mensaje de staff apartaría al agente: la respuesta del paciente quedaría sin contestar.
        assert message.sender == ChatMessage.Sender.AGENT
        assert message.raw['kind'] == 'booking_confirmed' and message.raw['notice_event'] == 'ev1'
        assert 'está confirmada' in message.body
        message.session.refresh_from_db()
        assert message.session.last_staff_message_at is None and message.session.agent_should_reply

    def test_it_does_not_touch_what_n8n_reads_about_reminders(self, appointment, templates, meta):
        _send(appointment, 'booking_confirmed')

        appointment.refresh_from_db()
        assert not appointment.reminder_24h_sent and not appointment.reminder_responded

    def test_the_same_event_is_not_sent_twice(self, appointment, templates, meta):
        assert _send(appointment, 'booking_confirmed', event='ev1') == 'sent'
        assert _send(appointment, 'booking_confirmed', event='ev1') == 'duplicate'

        assert meta.call_count == 1

    def test_two_different_events_are_two_notices(self, appointment, templates, meta):
        _send(appointment, 'booking_confirmed', event='ev1')
        _send(appointment, 'booking_confirmed', event='ev2')

        assert meta.call_count == 2


@pytest.mark.django_db
class TestWhatDoesNotGoOut:
    def test_clinic_without_a_marked_template_sends_nothing(self, appointment, connected, meta):
        assert _send(appointment, 'booking_confirmed') == 'no_template'
        meta.assert_not_called()

    def test_each_notice_has_its_own_switch(self, appointment, connected, meta):
        _template(connected, 'booking_cancelled')

        assert _send(appointment, 'booking_confirmed') == 'no_template'
        assert _send(appointment, 'booking_changed', previous_at=appointment.scheduled_at) == 'no_template'
        meta.assert_not_called()

    @pytest.mark.parametrize('status', [Status.PENDING, Status.REJECTED, Status.PAUSED, Status.DISABLED, Status.DRAFT])
    def test_template_must_be_approved(self, appointment, templates, meta, status):
        WhatsAppTemplate.objects.filter(purpose=Purpose.BOOKING_CONFIRMED).update(status=status)

        assert _send(appointment, 'booking_confirmed') == 'no_template'
        meta.assert_not_called()

    def test_clinic_without_whatsapp_credentials_sends_nothing(self, appointment, templates, meta, clinic_a):
        clinic_a.whatsapp_token = ''
        clinic_a.save()

        assert _send(appointment, 'booking_confirmed') == 'no_template'

    def test_no_phone_no_notice(self, appointment, templates, meta, patient_a):
        patient_a.phone = ''
        patient_a.save()

        assert _send(appointment, 'booking_confirmed') == 'no_phone'
        meta.assert_not_called()
        assert not ChatMessage.objects.exists()

    def test_a_missing_field_leaves_a_visible_note_instead_of_an_empty_variable(self, appointment, connected, meta):
        _template(
            connected, 'booking_confirmed', body='Cita con {{1}}.', variable_map={'1': 'professional'},
        )
        appointment.professional = None
        appointment.save()

        assert _send(appointment, 'booking_confirmed') == 'missing_data'

        meta.assert_not_called()
        note = ChatMessage.objects.get()
        assert note.status == ChatMessage.Status.FAILED and 'profesional' in note.error_message
        assert note.body.startswith('Aviso de cita confirmada')

    def test_the_change_notice_without_a_previous_time_is_not_sent(self, appointment, templates, meta):
        assert _send(appointment, 'booking_changed') == 'missing_data'
        meta.assert_not_called()

    def test_previous_fields_are_refused_outside_the_change_notice(self, appointment, connected, meta):
        _template(
            connected, 'booking_confirmed', body='Era {{1}}.', variable_map={'1': 'previous_time'},
        )

        # `previous_*` no se ofrece ahí; si llegara a guardarse, tampoco se envía con hueco.
        assert _send(appointment, 'booking_confirmed') == 'missing_data'
        meta.assert_not_called()

    def test_template_with_an_unassigned_variable_is_not_used(self, appointment, templates, meta):
        WhatsAppTemplate.objects.filter(purpose=Purpose.BOOKING_CONFIRMED).update(variable_map={'1': 'clinic_name'})

        assert _send(appointment, 'booking_confirmed') == 'no_template'


@pytest.mark.django_db
class TestStaleNotices:
    """Entre que se encola el aviso y sale puede haber pasado de todo."""

    def test_a_confirmed_notice_is_dropped_if_the_appointment_was_cancelled_meanwhile(self, appointment, templates, meta):
        appointment.status = Appointment.Status.CANCELLED
        appointment.save()

        assert _send(appointment, 'booking_confirmed') == 'stale'
        meta.assert_not_called()

    def test_a_cancelled_notice_is_dropped_if_the_appointment_is_alive_again(self, appointment, templates, meta):
        assert _send(appointment, 'booking_cancelled') == 'stale'

    def test_a_notice_for_an_old_time_is_dropped_after_the_appointment_moved(self, appointment, templates, meta):
        old = appointment.scheduled_at
        appointment.scheduled_at = old + timedelta(days=1)
        appointment.end_at = appointment.scheduled_at + timedelta(minutes=30)
        appointment.save()

        assert _send(appointment, 'booking_confirmed', scheduled_at=old) == 'stale'
        meta.assert_not_called()

    def test_nothing_is_sent_for_an_appointment_that_already_took_place(self, appointment, templates, meta):
        appointment.scheduled_at = timezone.now() - timedelta(hours=1)
        appointment.end_at = appointment.scheduled_at + timedelta(minutes=30)
        appointment.save()

        assert _send(appointment, 'booking_confirmed') == 'stale'

    def test_an_appointment_that_no_longer_exists_is_just_gone(self, appointment, templates, meta):
        pk, when = appointment.pk, appointment.scheduled_at
        appointment.delete()

        assert send_notice(pk, 'booking_confirmed', 'ev', when.isoformat(), None) == 'gone'


@pytest.mark.django_db
class TestRecipients:
    def test_booked_for_other_greets_the_requester_at_their_phone(self, appointment, templates, meta):
        appointment.patient = None
        appointment.booked_for_other = True
        appointment.patient_name = 'Padre de Ana'
        appointment.contact_name = 'Ana Pérez'
        appointment.patient_phone = '+34600999888'
        appointment.save()

        assert _send(appointment, 'booking_confirmed') == 'sent'

        _, phone, _, _, params = meta.call_args.args
        assert phone == '+34600999888' and params[0] == 'Ana'

    def test_an_appointment_without_a_file_uses_its_own_contact(self, appointment, templates, meta):
        appointment.patient = None
        appointment.patient_name = 'Luis Gómez'
        appointment.patient_phone = '+34600555444'
        appointment.save()

        assert _send(appointment, 'booking_confirmed') == 'sent'

        assert meta.call_args.args[1] == '+34600555444' and meta.call_args.args[4][0] == 'Luis'

    def test_other_clinics_templates_are_never_used(self, appointment, connected, clinic_b, meta):
        clinic_b.whatsapp_phone_number_id = '222'
        clinic_b.whatsapp_token = 'EAAG'
        clinic_b.save()
        _template(clinic_b, 'booking_confirmed')

        assert _send(appointment, 'booking_confirmed') == 'no_template'
        meta.assert_not_called()


@pytest.mark.django_db
class TestFailures:
    def test_a_meta_rejection_is_visible_and_counts_as_an_attempt(self, appointment, templates):
        with patch('agent.services.send_template', side_effect=WhatsAppError('Número inválido')):
            assert _send(appointment, 'booking_confirmed', event='ev1') == 'failed'

        message = ChatMessage.objects.get()
        assert message.status == ChatMessage.Status.FAILED and 'Número inválido' in message.error_message

    def test_a_failed_attempt_can_be_retried_and_then_goes_out_once(self, appointment, templates, meta):
        with patch('agent.services.send_template', side_effect=WhatsAppError('Caído')):
            assert _send(appointment, 'booking_confirmed', event='ev1') == 'failed'

        assert _send(appointment, 'booking_confirmed', event='ev1') == 'sent'
        assert _send(appointment, 'booking_confirmed', event='ev1') == 'duplicate'

        statuses = sorted(ChatMessage.objects.values_list('status', flat=True))
        assert statuses == [ChatMessage.Status.FAILED, ChatMessage.Status.SENT]

    def test_it_gives_up_after_the_maximum_attempts(self, appointment, templates):
        with patch('agent.services.send_template', side_effect=WhatsAppError('Caído')):
            results = [_send(appointment, 'booking_confirmed', event='ev1') for _ in range(MAX_ATTEMPTS + 2)]

        assert results == ['failed'] * MAX_ATTEMPTS + ['gave_up'] * 2
        assert ChatMessage.objects.count() == MAX_ATTEMPTS

    def test_the_celery_task_retries_a_failure(self, appointment, templates):
        calls = []

        def flaky(*args):
            calls.append(args)
            if len(calls) == 1:
                raise WhatsAppError('Caído')
            return f'wamid.{uuid.uuid4().hex}'

        with patch('agent.services.send_template', side_effect=flaky):
            send_appointment_notice.delay(
                appointment_id=str(appointment.pk), kind='booking_confirmed', event_id='ev1',
                scheduled_at=appointment.scheduled_at.isoformat(),
            )

        assert len(calls) == 2
        assert ChatMessage.objects.filter(status=ChatMessage.Status.SENT).count() == 1


# ---------------------------------------------------------------------------
# Quién lo dispara: los ganchos de `appointments.services`
# ---------------------------------------------------------------------------

@pytest.fixture
def queued():
    """Los avisos que se encolan, como `(tipo, hora anterior)`."""
    with patch('notifications.whatsapp_notices.send_appointment_notice.delay') as delay:
        class Spy:
            def kinds(self):
                return [call.kwargs['kind'] for call in delay.call_args_list]

            def previous(self):
                return [call.kwargs['previous_at'] for call in delay.call_args_list]

        yield Spy()


@pytest.fixture
def lunes_10h(clinic_a):
    today = timezone.localdate()
    monday = today + timedelta(days=(0 - today.weekday()) % 7 or 7)
    return datetime.combine(monday, time(10, 0), tzinfo=ZoneInfo(clinic_a.timezone))


def _book(clinic, patient, service, professional, when, *, source, **extra):
    return create_appointment(
        clinic=clinic, patient=patient, service=service, professional=professional,
        scheduled_at=when, require_online_booking=False, source=source, **extra,
    )


@pytest.mark.django_db
class TestWhoTriggersIt:
    def test_an_appointment_the_clinic_creates_is_announced_as_confirmed(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)

        assert queued.kinds() == ['booking_confirmed']

    def test_one_the_agent_books_is_not_announced_at_creation(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued, django_capture_on_commit_callbacks
    ):
        """Nace `pending`: el paciente ya tiene su respuesta en el chat y la clínica aún no la ha validado."""
        with django_capture_on_commit_callbacks(execute=True):
            _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        assert queued.kinds() == []

    def test_it_is_announced_when_the_clinic_validates_it(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued,
        admin_user, django_capture_on_commit_callbacks,
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        with django_capture_on_commit_callbacks(execute=True):
            confirm_by_clinic(cita, user=admin_user)
            confirm_by_clinic(cita, user=admin_user)  # idempotente: no hay un segundo aviso

        assert queued.kinds() == ['booking_confirmed']

    @pytest.mark.parametrize('actor, expected', [
        (Actor.STAFF, ['booking_cancelled']),
        (Actor.PATIENT, []),  # lo pidió él: ya sabe que está cancelada
        (Actor.SYSTEM, []),  # un hold caducado nunca llegó a ser suyo
        (Actor.AGENT, []),
    ])
    def test_a_cancellation_is_announced_only_if_the_clinic_did_it(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued,
        django_capture_on_commit_callbacks, actor, expected,
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)
        queued_before = len(queued.kinds())

        with django_capture_on_commit_callbacks(execute=True):
            cancel_appointment(cita, actor=actor)
            cancel_appointment(cita, actor=actor)  # idempotente

        assert queued.kinds()[queued_before:] == expected

    def test_a_cancellation_can_be_silenced(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued, django_capture_on_commit_callbacks
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        with django_capture_on_commit_callbacks(execute=True):
            cancel_appointment(cita, actor=Actor.STAFF, notify=False)

        assert queued.kinds() == []

    def test_archiving_a_patient_does_not_message_them_about_their_appointments(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued,
        admin_user, django_capture_on_commit_callbacks,
    ):
        from patients.services import archive_patient

        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        with django_capture_on_commit_callbacks(execute=True):
            assert archive_patient(patient_a, user=admin_user) == 1

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CANCELLED and queued.kinds() == []

    def test_a_time_change_by_the_clinic_is_announced_with_the_previous_time(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued, django_capture_on_commit_callbacks
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)
        queued_before = len(queued.kinds())

        with django_capture_on_commit_callbacks(execute=True):
            reschedule_appointment(
                cita, scheduled_at=lunes_10h + timedelta(hours=1), actor=Actor.STAFF, require_online_booking=False,
            )

        assert queued.kinds()[queued_before:] == ['booking_changed']
        assert datetime.fromisoformat(queued.previous()[-1]) == lunes_10h

    def test_a_time_change_the_patient_asked_for_is_not_announced(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued, django_capture_on_commit_callbacks
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)
        queued_before = len(queued.kinds())

        with django_capture_on_commit_callbacks(execute=True):
            reschedule_appointment(
                cita, scheduled_at=lunes_10h + timedelta(hours=1), actor=Actor.AGENT, require_online_booking=False,
            )

        assert queued.kinds()[queued_before:] == []

    def test_staff_moving_the_time_through_the_api_is_announced_and_the_agent_is_not(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, queued,
        admin_client, api_client, django_capture_on_commit_callbacks,
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)
        queued_before = len(queued.kinds())
        new_time = (lunes_10h + timedelta(hours=1)).isoformat()

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.patch(f'/api/appointments/{cita.pk}/', {'scheduled_at': new_time}, format='json')
        assert response.status_code == 200, response.content
        assert queued.kinds()[queued_before:] == ['booking_changed']
        assert datetime.fromisoformat(queued.previous()[-1]) == lunes_10h

        api_client.force_authenticate(user=None)
        api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        agent_time = (lunes_10h + timedelta(hours=2)).isoformat()
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.patch(f'/api/appointments/{cita.pk}/', {'scheduled_at': agent_time}, format='json')
        assert response.status_code == 200, response.content
        assert queued.kinds()[queued_before:] == ['booking_changed']  # el del agente no suma

    def test_a_broker_outage_does_not_break_the_booking(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, admin_user, django_capture_on_commit_callbacks
    ):
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        with patch(
            'notifications.whatsapp_notices.send_appointment_notice.delay', side_effect=ConnectionError('redis'),
        ):
            with django_capture_on_commit_callbacks(execute=True):
                confirm_by_clinic(cita, user=admin_user)

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CONFIRMED


@pytest.mark.django_db
class TestFromTheActionToTheChat:
    def test_confirming_an_appointment_ends_with_a_message_in_the_thread(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, templates, meta,
        admin_user, django_capture_on_commit_callbacks,
    ):
        patient_a.phone = PHONE
        patient_a.save()
        cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.AGENT)

        with django_capture_on_commit_callbacks(execute=True):
            confirm_by_clinic(cita, user=admin_user)

        message = ChatMessage.objects.get(message_type='template')
        assert message.raw['kind'] == 'booking_confirmed' and message.status == ChatMessage.Status.SENT
        assert meta.call_args.args[1] == PHONE

    def test_the_whole_staff_flow_announces_confirmation_change_and_cancellation_in_order(
        self, clinic_a, patient_a, service_a, professional_a, lunes_10h, templates, meta,
        django_capture_on_commit_callbacks,
    ):
        patient_a.phone = PHONE
        patient_a.save()

        with django_capture_on_commit_callbacks(execute=True):
            cita = _book(clinic_a, patient_a, service_a, professional_a, lunes_10h, source=Appointment.Source.STAFF)
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_appointment(
                cita, scheduled_at=lunes_10h + timedelta(hours=1), actor=Actor.STAFF, require_online_booking=False,
            )
        with django_capture_on_commit_callbacks(execute=True):
            cancel_appointment(cita, actor=Actor.STAFF)

        kinds = list(ChatMessage.objects.order_by('created_at').values_list('raw__kind', flat=True))
        assert kinds == ['booking_confirmed', 'booking_changed', 'booking_cancelled']


# ---------------------------------------------------------------------------
# El panel: marcar una plantilla para cada aviso
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMarkingTheTemplate:
    def _url(self, template, uso=None):
        url = reverse('agent_settings:template-reminder', args=[template.pk])
        return f'{url}?uso={uso}' if uso else url

    def _approved(self, clinic, name, body):
        return WhatsAppTemplate.objects.create(clinic=clinic, name=name, body=body, status=Status.APPROVED, meta_template_id='1')

    def test_saving_marks_it_for_that_notice_and_stores_the_mapping(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'cancelada', 'Hola {{1}}, cancelamos tu cita del {{2}}.')
        client.force_login(admin_user)

        response = client.post(self._url(template), {'uso': 'booking_cancelled', 'field_1': 'patient_first_name', 'field_2': 'date'})

        assert response.status_code == 302
        template.refresh_from_db()
        assert template.purpose == Purpose.BOOKING_CANCELLED
        assert template.variable_map == {'1': 'patient_first_name', '2': 'date'}

    def test_each_notice_has_its_own_template_and_taking_one_releases_only_that_one(self, client, admin_user, clinic_a):
        old = self._approved(clinic_a, 'vieja', 'Hola {{1}}.')
        other = self._approved(clinic_a, 'otra', 'Hola {{1}}.')
        new = self._approved(clinic_a, 'nueva', 'Hola {{1}}.')
        client.force_login(admin_user)
        client.post(self._url(old), {'uso': 'booking_confirmed', 'field_1': 'patient_first_name'})
        client.post(self._url(other), {'uso': 'booking_cancelled', 'field_1': 'patient_first_name'})

        client.post(self._url(new), {'uso': 'booking_confirmed', 'field_1': 'patient_first_name'})

        purposes = {t.name: t.purpose for t in WhatsAppTemplate.objects.all()}
        assert purposes == {'vieja': '', 'otra': 'booking_cancelled', 'nueva': 'booking_confirmed'}

    def test_previous_time_fields_are_only_offered_for_the_change_notice(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'cambio', 'Era {{1}}.')
        client.force_login(admin_user)

        changed = client.get(self._url(template, 'booking_changed')).content.decode()
        confirmed = client.get(self._url(template, 'booking_confirmed')).content.decode()

        assert 'previous_time' in changed and 'Hora anterior de la cita' in changed
        assert 'Hora anterior de la cita' not in confirmed

    def test_a_previous_time_field_cannot_be_saved_for_another_notice(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'mala', 'Era {{1}}.')
        client.force_login(admin_user)

        response = client.post(self._url(template), {'uso': 'booking_confirmed', 'field_1': 'previous_time'})

        assert response.status_code == 200  # vuelve al formulario con el error
        template.refresh_from_db()
        assert template.purpose == ''

    def test_a_made_up_use_is_refused(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'rara', 'Hola {{1}}.')
        client.force_login(admin_user)

        response = client.post(self._url(template), {'uso': 'marketing', 'field_1': 'patient_first_name'})

        assert response.status_code == 302
        template.refresh_from_db()
        assert template.purpose == ''

    def test_clearing_one_turns_only_that_notice_off(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'cancelada', 'Hola {{1}}.')
        client.force_login(admin_user)
        client.post(self._url(template), {'uso': 'booking_cancelled', 'field_1': 'patient_first_name'})

        client.post(reverse('agent_settings:template-clear-purpose', args=[template.pk]))

        template.refresh_from_db()
        assert template.purpose == ''

    def test_the_list_shows_which_automatic_messages_are_on(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'cancelada', 'Hola {{1}}.')
        client.force_login(admin_user)
        client.post(self._url(template), {'uso': 'booking_cancelled', 'field_1': 'patient_first_name'})

        uses = {u['name']: u['state'] for u in client.get(reverse('agent_settings:templates')).context['automatic_uses']}

        assert uses == {
            'Recordatorio de cita': 'off',
            'Aviso de cita confirmada': 'off',
            'Aviso de cambio de cita': 'off',
            'Aviso de cita cancelada': 'on',
        }

    def test_each_automatic_message_has_its_own_icon(self, client, admin_user):
        client.force_login(admin_user)

        html = client.get(reverse('agent_settings:templates')).content.decode()

        # campana (recordatorio), check (confirmada), flechas (cambio) y aspa (cancelada)
        for icon in ('M14.857 17.082', 'M9 12.75 11.25 15', 'M16.023 9.348', 'm9.75 9.75 4.5 4.5'):
            assert icon in html

    def test_a_marked_template_that_meta_paused_shows_as_not_sending(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, 'cancelada', 'Hola {{1}}.')
        client.force_login(admin_user)
        client.post(self._url(template), {'uso': 'booking_cancelled', 'field_1': 'patient_first_name'})
        template.refresh_from_db()
        template.status = Status.PAUSED
        template.save()

        uses = {u['name']: u['state'] for u in client.get(reverse('agent_settings:templates')).context['automatic_uses']}

        assert uses['Aviso de cita cancelada'] == 'blocked'

    def test_staff_and_other_clinics_cannot(self, client, staff_user, admin_user_b, clinic_a):
        template = self._approved(clinic_a, 'cancelada', 'Hola {{1}}.')
        client.force_login(staff_user)
        assert client.post(self._url(template), {'uso': 'booking_cancelled', 'field_1': 'clinic_name'}).status_code == 403
        client.force_login(admin_user_b)
        assert client.get(self._url(template, 'booking_cancelled')).status_code == 404
