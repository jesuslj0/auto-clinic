"""Recordatorio de cita de 24 h por WhatsApp, enviado desde Django con plantilla.

Cubre lo que el workflow de n8n que sustituye no hacía bien: no repetir el aviso,
marcar `reminder_24h_sent` (de lo que depende el orquestador para entender el
«sí» del paciente), no dejar sin enviar en silencio y respetar la regla de qué
citas se recuerdan.
"""
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from agent import whatsapp
from agent.models import ChatMessage, WhatsAppTemplate
from appointments.models import Appointment, AppointmentStatusHistory
from notifications import tasks
from notifications.whatsapp_reminders import (
    MAX_ATTEMPTS,
    reminder_values,
    send_due_reminders,
    send_reminder,
)

Status = WhatsAppTemplate.Status

DEFAULT_MAP = {'1': 'patient_first_name', '2': 'clinic_name', '3': 'date', '4': 'time'}

BODY = 'Hola {{1}}, te recordamos tu cita en {{2}} el {{3}} a las {{4}}. Responde SÍ para confirmar o NO para cancelar.'


@pytest.fixture
def connected(clinic_a):
    clinic_a.whatsapp_phone_number_id = '111'
    clinic_a.whatsapp_token = 'EAAG'
    clinic_a.save()
    return clinic_a


@pytest.fixture
def reminder_template(connected):
    return WhatsAppTemplate.objects.create(
        clinic=connected, name='recordatorio_cita_24h', body=BODY,
        body_examples=['Ana', 'Clínica', 'lunes 1 de junio', '10:30'],
        status=Status.APPROVED, meta_template_id='9',
        purpose=WhatsAppTemplate.Purpose.REMINDER_24H, variable_map=DEFAULT_MAP,
    )


def _appointment(clinic, patient, service, *, hours=23, status=Appointment.Status.CONFIRMED, **extra):
    when = timezone.now() + timedelta(hours=hours)
    return Appointment.objects.create(
        clinic=clinic, patient=patient, service=service,
        scheduled_at=when, end_at=when + timedelta(minutes=30), status=status, **extra,
    )


@pytest.fixture
def due(clinic_a, patient_a, service_a):
    patient_a.phone = '+34600111222'
    patient_a.save()
    return _appointment(clinic_a, patient_a, service_a)


@pytest.fixture
def meta():
    # Un wamid distinto por envío, como hace Meta: es único en `chat_messages`.
    with patch('agent.services.send_template', side_effect=lambda *a: f'wamid.{uuid.uuid4().hex}') as send:
        yield send


@pytest.mark.django_db
class TestSending:
    def test_sends_the_template_and_marks_the_appointment(self, due, reminder_template, meta):
        assert send_due_reminders() == {'sent': 1}

        due.refresh_from_db()
        assert due.reminder_24h_sent is True and due.reminder_24h_sent_at is not None
        meta.assert_called_once()
        clinic, phone, name, language, params = meta.call_args.args
        assert (phone, name, language) == ('+34600111222', 'recordatorio_cita_24h', 'es')
        assert params[:2] == ['John', 'Clinic Alpha'] and params[3].count(':') == 1

    def test_leaves_a_template_message_in_the_thread_without_pausing_the_agent(self, due, reminder_template, meta):
        send_due_reminders()

        message = ChatMessage.objects.get(message_type='template')
        assert message.status == ChatMessage.Status.SENT
        assert message.sender == ChatMessage.Sender.AGENT
        assert message.raw['kind'] == 'reminder_24h' and message.raw['appointment_id'] == str(due.pk)
        assert 'Responde SÍ' in message.body
        message.session.refresh_from_db()
        # Un mensaje de staff apartaría al agente: el «sí» del paciente se quedaría sin respuesta.
        assert message.session.last_staff_message_at is None
        assert message.session.agent_should_reply

    def test_does_not_send_twice(self, due, reminder_template, meta):
        send_due_reminders()
        send_due_reminders()

        assert meta.call_count == 1

    def test_the_orchestrators_reply_lookup_now_finds_the_appointment(self, due, reminder_template, meta, admin_client, clinic_a):
        """n8n decide «esto es la respuesta al recordatorio» con estos filtros exactos."""
        url = '/api/appointments/'
        params = {'patient_phone': '+34600111222', 'reminder_24h_sent': 'true',
                  'reminder_responded': 'false', 'status': 'confirmed'}
        before = admin_client.get(url, params).json()

        send_due_reminders()
        after = admin_client.get(url, params).json()

        assert before['count'] == 0 and after['count'] == 1

    def test_pending_reminders_endpoint_no_longer_lists_it(self, due, reminder_template, meta, admin_client):
        send_due_reminders()

        assert admin_client.get('/api/appointments/pending-reminders/').json()['count'] == 0


@pytest.mark.django_db
class TestWhichAppointments:
    def test_only_the_24h_window(self, clinic_a, patient_a, service_a, reminder_template, meta):
        patient_a.phone = '+34600111222'
        patient_a.save()
        for hours in (5, 21, 25, 48):
            _appointment(clinic_a, patient_a, service_a, hours=hours)
        inside = _appointment(clinic_a, patient_a, service_a, hours=23)

        assert send_due_reminders() == {'sent': 1}
        inside.refresh_from_db()
        assert inside.reminder_24h_sent

    def test_not_pending_cancelled_or_already_confirmed_by_the_patient(self, clinic_a, patient_a, service_a, reminder_template, meta):
        patient_a.phone = '+34600111222'
        patient_a.save()
        _appointment(clinic_a, patient_a, service_a, status=Appointment.Status.PENDING)
        _appointment(clinic_a, patient_a, service_a, status=Appointment.Status.CANCELLED)
        _appointment(clinic_a, patient_a, service_a, patient_confirmed_at=timezone.now())

        assert send_due_reminders() == {}
        meta.assert_not_called()

    def test_a_rescheduled_appointment_is_reminded_again_for_the_new_time(
        self, due, reminder_template, meta, professional_a
    ):
        from appointments.services import reschedule_appointment

        due.professional = professional_a
        due.save()
        # Una cita en firme de verdad tiene su paso por `confirmed` en el historial,
        # que es lo que permite seguir recordándola después de moverla.
        AppointmentStatusHistory.objects.create(
            appointment=due, from_status=Appointment.Status.PENDING,
            to_status=Appointment.Status.CONFIRMED, actor=AppointmentStatusHistory.Actor.STAFF,
        )
        send_due_reminders()
        assert meta.call_count == 1

        # Se mueve a otra hora, también dentro de la ventana: la reprogramación
        # limpia el flag y el paciente vuelve a recibir el aviso con la hora nueva.
        reschedule_appointment(
            due, scheduled_at=timezone.now() + timedelta(hours=22, minutes=30), require_online_booking=False
        )
        assert send_due_reminders() == {'sent': 1}
        assert meta.call_count == 2


@pytest.mark.django_db
class TestWhatDoesNotGoOut:
    def test_clinic_without_a_marked_template_sends_nothing(self, due, connected, meta):
        assert send_due_reminders() == {'no_template': 1}
        meta.assert_not_called()
        due.refresh_from_db()
        assert not due.reminder_24h_sent  # se enviará en cuanto haya plantilla, mientras dure la ventana

    @pytest.mark.parametrize('status', [Status.PENDING, Status.REJECTED, Status.PAUSED, Status.DISABLED, Status.DRAFT])
    def test_template_must_be_approved(self, due, reminder_template, meta, status):
        reminder_template.status = status
        reminder_template.save()

        assert send_due_reminders() == {'no_template': 1}
        meta.assert_not_called()

    def test_template_with_an_unassigned_variable_is_not_used(self, due, reminder_template, meta):
        reminder_template.variable_map = {'1': 'patient_first_name'}
        reminder_template.save()

        assert send_due_reminders() == {'no_template': 1}
        meta.assert_not_called()

    def test_template_with_a_made_up_field_is_not_used(self, due, reminder_template, meta):
        reminder_template.variable_map = {**DEFAULT_MAP, '2': 'service'}
        reminder_template.save()

        assert send_due_reminders() == {'no_template': 1}

    def test_clinic_without_whatsapp_credentials(self, due, reminder_template, meta, clinic_a):
        clinic_a.whatsapp_token = ''
        clinic_a.save()

        assert send_due_reminders() == {'no_template': 1}

    def test_no_phone(self, clinic_a, patient_a, service_a, reminder_template, meta):
        patient_a.phone = ''
        patient_a.save()
        _appointment(clinic_a, patient_a, service_a)

        assert send_due_reminders() == {'no_phone': 1}

    def test_other_clinics_use_their_own_template(self, due, reminder_template, meta, clinic_b, patient_b, service_b):
        patient_b.phone = '+34600999888'
        patient_b.save()
        _appointment(clinic_b, patient_b, service_b)

        # La clínica B no tiene plantilla marcada: solo sale el de A.
        assert send_due_reminders() == {'sent': 1, 'no_template': 1}
        assert meta.call_args.args[0] == reminder_template.clinic


@pytest.mark.django_db
class TestFailures:
    def test_failure_leaves_a_visible_failed_message_and_retries(self, due, reminder_template):
        with patch('agent.services.send_template', side_effect=whatsapp.WhatsAppError('plantilla pausada')):
            assert send_due_reminders() == {'failed': 1}

        message = ChatMessage.objects.get(message_type='template')
        assert (message.status, message.error_message) == ('failed', 'plantilla pausada')
        due.refresh_from_db()
        assert not due.reminder_24h_sent

        with patch('agent.services.send_template', return_value='wamid.OK'):
            assert send_due_reminders() == {'sent': 1}

    def test_gives_up_after_the_maximum_attempts(self, due, reminder_template):
        with patch('agent.services.send_template', side_effect=whatsapp.WhatsAppError('número no válido')) as send:
            results = [send_due_reminders() for _ in range(MAX_ATTEMPTS + 2)]

        assert [list(r) for r in results] == [['failed']] * MAX_ATTEMPTS + [['gave_up']] * 2
        assert send.call_count == MAX_ATTEMPTS

    def test_one_failure_does_not_stop_the_others(self, clinic_a, patient_a, service_a, due, reminder_template):
        second = _appointment(clinic_a, patient_a, service_a, hours=22.5)
        calls = iter([whatsapp.WhatsAppError('x'), 'wamid.2'])

        def fake(*args):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        with patch('agent.services.send_template', side_effect=fake):
            assert send_due_reminders() == {'failed': 1, 'sent': 1}


@pytest.mark.django_db
class TestRecipients:
    def test_variables(self, due, reminder_template):
        values = reminder_values(due, reminder_template)

        assert values[0] == 'John' and values[1] == 'Clinic Alpha'
        assert values[2].split()[0] in {'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'}
        assert ' de ' in values[2]

    def test_appointment_without_a_patient_file_uses_the_contact(self, clinic_a, service_a, reminder_template, meta):
        _appointment(clinic_a, None, service_a, patient_name='Rosa Pérez', patient_phone='+34611222333')

        assert send_due_reminders() == {'sent': 1}
        clinic, phone, name, language, params = meta.call_args.args
        assert phone == '+34611222333' and params[0] == 'Rosa'

    def test_booked_for_other_goes_to_who_asked_for_it(self, clinic_a, service_a, reminder_template, meta):
        _appointment(
            clinic_a, None, service_a, patient_name='Antonio Pérez', patient_phone='+34611222333',
            booked_for_other=True, contact_name='María Pérez', contact_relationship='child',
        )

        send_due_reminders()

        clinic, phone, name, language, params = meta.call_args.args
        assert phone == '+34611222333' and params[0] == 'María'

    def test_local_time_not_utc(self, clinic_a, patient_a, service_a, reminder_template):
        clinic_a.timezone = 'Europe/Madrid'
        appointment = _appointment(clinic_a, patient_a, service_a)
        appointment.scheduled_at = datetime(2026, 7, 6, 8, 30, tzinfo=dt_timezone.utc)  # 10:30 en Madrid (CEST)

        assert reminder_values(appointment, reminder_template)[2:] == ['lunes 6 de julio', '10:30']


@pytest.mark.django_db
class TestVariableMapping:
    def test_each_variable_gets_the_field_it_is_mapped_to_in_that_order(
        self, clinic_a, patient_a, service_a, professional_a, reminder_template, meta
    ):
        patient_a.phone = '+34600111222'
        patient_a.save()
        clinic_a.address, clinic_a.city = 'C/ Sierpes 12', 'Sevilla'
        clinic_a.save()
        reminder_template.body = 'Con {{1}} en {{2}}: {{3}} a las {{4}}, {{5}}. Responde SÍ o NO a este aviso.'
        reminder_template.variable_map = {
            '1': 'professional', '2': 'clinic_address', '3': 'date', '4': 'time', '5': 'patient_full_name',
        }
        reminder_template.save()
        _appointment(clinic_a, patient_a, service_a, professional=professional_a)

        assert send_due_reminders() == {'sent': 1}

        params = meta.call_args.args[-1]
        assert params[0] == professional_a.display_name
        assert params[1] == 'C/ Sierpes 12, Sevilla'
        assert params[4] == 'John Doe'

    def test_template_without_variables_is_sent_as_is(self, due, reminder_template, meta):
        reminder_template.body = 'Te recordamos que mañana tienes cita en la clínica. Responde SÍ o NO.'
        reminder_template.variable_map = {}
        reminder_template.save()

        assert send_due_reminders() == {'sent': 1}
        assert meta.call_args.args[-1] == []

    def test_missing_data_is_not_sent_with_a_hole_and_leaves_a_visible_note(self, due, reminder_template, meta):
        reminder_template.variable_map = {**DEFAULT_MAP, '2': 'professional'}  # la cita no tiene profesional
        reminder_template.save()

        assert send_due_reminders() == {'missing_data': 1}

        meta.assert_not_called()
        message = ChatMessage.objects.get(message_type='template')
        assert message.status == ChatMessage.Status.FAILED
        assert 'profesional' in message.error_message
        due.refresh_from_db()
        assert not due.reminder_24h_sent

    def test_missing_data_gives_up_after_the_maximum_attempts(self, due, reminder_template, meta):
        reminder_template.variable_map = {**DEFAULT_MAP, '2': 'professional'}
        reminder_template.save()

        results = [send_due_reminders() for _ in range(MAX_ATTEMPTS + 1)]

        assert [list(r) for r in results] == [['missing_data']] * MAX_ATTEMPTS + [['gave_up']]

    def test_the_service_and_clinical_data_are_not_available(self):
        from appointments.reminder_fields import BY_KEY

        assert not {'service', 'service_name', 'notes', 'diagnosis'} & set(BY_KEY)


@pytest.mark.django_db
class TestTaskAndSchedule:
    def test_the_task_runs_it(self, due, reminder_template, meta):
        assert tasks.send_whatsapp_reminders_24h() == {'sent': 1}

    def test_it_is_in_the_beat_schedule_and_the_old_email_tasks_are_not(self, settings):
        schedule = settings.CELERY_BEAT_SCHEDULE
        assert schedule['send-whatsapp-reminders-24h']['task'] == 'notifications.tasks.send_whatsapp_reminders_24h'
        assert not any('dispatch_' in entry['task'] for entry in schedule.values())


@pytest.mark.django_db
class TestMarkingTheTemplate:
    def _approved(self, clinic, name='recordatorio', body=BODY, **extra):
        return WhatsAppTemplate.objects.create(clinic=clinic, name=name, body=body, status=Status.APPROVED, **extra)

    def _url(self, template):
        return reverse('agent_settings:template-reminder', args=[template.pk])

    def _post(self, client, template, **fields):
        return client.post(self._url(template), {f'field_{k}': v for k, v in fields.items()})

    def test_the_page_suggests_the_usual_order(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a)
        client.force_login(admin_user)

        response = client.get(self._url(template))

        assert response.status_code == 200
        assert response.context['chosen'] == DEFAULT_MAP
        html = response.content.decode()
        assert 'name="field_4"' in html and 'name="field_5"' not in html
        assert 'Nombre de la clínica' in html and 'Profesional' in html
        offered = {f['key'] for f in response.context['fields']}
        assert 'service' not in offered and 'patient_first_name' in offered  # el servicio, fuera a propósito

    def test_saving_marks_it_and_stores_the_mapping(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a)
        client.force_login(admin_user)

        response = self._post(client, template, **{'1': 'patient_full_name', '2': 'clinic_name', '3': 'date', '4': 'time'})

        template.refresh_from_db()
        assert response.status_code == 302
        assert template.purpose == WhatsAppTemplate.Purpose.REMINDER_24H
        assert template.variable_map['1'] == 'patient_full_name'

    def test_the_previous_one_is_released(self, client, admin_user, clinic_a):
        old = self._approved(clinic_a, 'vieja', purpose=WhatsAppTemplate.Purpose.REMINDER_24H, variable_map=DEFAULT_MAP)
        new = self._approved(clinic_a, 'nueva')
        client.force_login(admin_user)

        self._post(client, new, **DEFAULT_MAP)

        old.refresh_from_db(), new.refresh_from_db()
        assert (old.purpose, new.purpose) == ('', WhatsAppTemplate.Purpose.REMINDER_24H)

    @pytest.mark.parametrize('bad', [{'2': 'service'}, {'2': ''}, {'2': 'notes'}])
    def test_rejects_unknown_or_missing_fields(self, client, admin_user, clinic_a, bad):
        template = self._approved(clinic_a)
        client.force_login(admin_user)

        response = self._post(client, template, **{**DEFAULT_MAP, **bad})

        assert response.status_code == 200
        template.refresh_from_db()
        assert template.purpose == '' and template.variable_map == {}

    @pytest.mark.parametrize('kwargs', [{'status': Status.PENDING}, {'status': Status.DRAFT}])
    def test_refuses_what_could_not_be_sent(self, client, admin_user, clinic_a, kwargs):
        template = self._approved(clinic_a)
        for field, value in kwargs.items():
            setattr(template, field, value)
        template.save()
        client.force_login(admin_user)

        assert self._post(client, template, **DEFAULT_MAP).status_code == 302
        template.refresh_from_db()
        assert template.purpose == ''

    def test_any_number_of_variables_is_fine(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, body='Hola {{1}}, mañana a las {{2}} te esperamos en la clínica.')
        client.force_login(admin_user)

        self._post(client, template, **{'1': 'patient_first_name', '2': 'time'})

        template.refresh_from_db()
        assert template.purpose and template.variable_map == {'1': 'patient_first_name', '2': 'time'}

    def test_clearing_it_turns_the_reminders_off(self, client, admin_user, clinic_a):
        template = self._approved(clinic_a, purpose=WhatsAppTemplate.Purpose.REMINDER_24H, variable_map=DEFAULT_MAP)
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-clear-purpose', args=[template.pk]))

        template.refresh_from_db()
        assert template.purpose == ''

    def test_staff_and_other_clinics_cannot(self, client, staff_user, admin_user_b, clinic_a):
        template = self._approved(clinic_a)

        client.force_login(staff_user)
        assert client.get(self._url(template)).status_code == 403
        assert self._post(client, template, **DEFAULT_MAP).status_code == 403
        client.force_login(admin_user_b)
        assert client.get(self._url(template)).status_code == 404

    def test_the_list_offers_the_link_only_for_approved_templates(self, client, admin_user, clinic_a):
        self._approved(clinic_a, 'buena')
        WhatsAppTemplate.objects.create(clinic=clinic_a, name='borrador', body='Todavía sin enviar a Meta ni nada.')
        client.force_login(admin_user)

        html = client.get(reverse('agent_settings:templates')).content.decode()

        # El menú «Usar como…» solo cuelga de la aprobada, no del borrador.
        assert html.count('>Usar como…</summary>') == 1
