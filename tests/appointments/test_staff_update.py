"""Modificar y reagendar una cita desde el panel (staff).

Cubre `update_appointment()` y la pantalla `appointments:update`: valida como el
alta, no marca la cita como `rescheduled`, olvida lo que dependía de la hora vieja,
avisa al paciente solo si la hora se mueve y la clínica ya la había confirmado, y
la cita de otra clínica es un 404.
"""
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, WhatsAppTemplate
from appointments.models import Appointment, Professional, ProfessionalSchedule
from appointments.services import (
    InvalidTransition,
    SlotUnavailable,
    cancel_appointment,
    create_appointment,
    update_appointment,
)
from core.models import User
from services.models import Service

PHONE = '+34600111222'


@pytest.fixture
def madrid(clinic_a):
    return ZoneInfo(clinic_a.timezone)


@pytest.fixture
def lunes(madrid):
    today = timezone.localdate()
    return today + timedelta(days=(0 - today.weekday()) % 7 or 7)


def at(day, hour, madrid, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=madrid)


@pytest.fixture
def prof(clinic_a, service_a):
    user = User.objects.create_user(email='prof@alpha.test', password='pass', clinic=clinic_a)
    professional = user.professional_profile
    professional.services.add(service_a)
    ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=0, start_time=time(9, 0), end_time=time(14, 0),
    )
    return professional


@pytest.fixture
def cita(clinic_a, patient_a, service_a, prof, lunes, madrid):
    """Cita en firme del staff, el lunes a las 10:00."""
    patient_a.phone = PHONE
    patient_a.save()
    return create_appointment(
        clinic=clinic_a, patient=patient_a, service=service_a, professional=prof,
        scheduled_at=at(lunes, 10, madrid), require_online_booking=False, source=Appointment.Source.STAFF,
    )


@pytest.fixture
def queued():
    """Los avisos que se encolan."""
    with patch('notifications.whatsapp_notices.send_appointment_notice.delay') as delay:
        yield delay


@pytest.fixture
def commits(django_capture_on_commit_callbacks):
    return lambda: django_capture_on_commit_callbacks(execute=True)


@pytest.mark.django_db
class TestUpdateAppointment:
    def test_moves_the_time_and_recalculates_the_end(self, cita, lunes, madrid, commits, queued):
        with commits():
            updated = update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 11, madrid)
        assert cita.end_at == at(lunes, 11, madrid) + timedelta(minutes=30)
        assert updated.pk == cita.pk

    def test_the_status_is_kept_and_not_flagged_as_rescheduled(self, cita, lunes, madrid, commits, queued):
        """Esa marca avisa a la clínica de lo que hizo el agente; aquí la clínica ya lo sabe."""
        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CONFIRMED

    def test_a_pending_appointment_stays_pending(self, cita, lunes, madrid, commits, queued):
        cita.status = Appointment.Status.PENDING
        cita.save()

        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.PENDING

    def test_what_was_known_about_the_old_time_is_forgotten(self, cita, lunes, madrid, commits, queued):
        cita.reminder_24h_sent = True
        cita.reminder_24h_sent_at = timezone.now()
        cita.reminder_responded = True
        cita.patient_confirmed_at = timezone.now()
        cita.hold_expires_at = timezone.now() + timedelta(hours=1)
        cita.save()

        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert not cita.reminder_24h_sent and cita.reminder_24h_sent_at is None
        assert not cita.reminder_responded and cita.patient_confirmed_at is None
        assert cita.hold_expires_at is None

    def test_changing_only_the_notes_touches_nothing_else(self, cita, commits, queued):
        cita.reminder_24h_sent = True
        cita.patient_confirmed_at = timezone.now()
        cita.save()

        with commits():
            update_appointment(cita, notes='Trae las radiografías')

        cita.refresh_from_db()
        assert cita.notes == 'Trae las radiografías'
        assert cita.reminder_24h_sent and cita.patient_confirmed_at is not None
        queued.assert_not_called()

    def test_a_noop_writes_nothing(self, cita, commits, queued):
        before = cita.updated_at

        with commits():
            update_appointment(cita, scheduled_at=cita.scheduled_at, professional=cita.professional)

        cita.refresh_from_db()
        assert cita.updated_at == before
        queued.assert_not_called()

    def test_changing_the_service_recalculates_the_duration(self, cita, clinic_a, prof, commits, queued):
        longer = Service.objects.create(clinic=clinic_a, name='Larga', duration_minutes=60, price='80.00')
        prof.services.add(longer)

        with commits():
            update_appointment(cita, service=longer)

        cita.refresh_from_db()
        assert cita.service == longer
        assert cita.end_at == cita.scheduled_at + timedelta(minutes=60)

    def test_changing_the_professional(self, cita, clinic_a, service_a, lunes, madrid, commits, queued):
        other = User.objects.create_user(email='otro@alpha.test', password='pass', clinic=clinic_a).professional_profile
        other.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=other, day_of_week=0, start_time=time(9, 0), end_time=time(14, 0),
        )

        with commits():
            update_appointment(cita, professional=other)

        cita.refresh_from_db()
        assert cita.professional == other
        queued.assert_not_called()  # el mensaje habla de fecha y hora, y eso no ha cambiado

    # -- las mismas reglas que el alta --------------------------------------

    def test_an_occupied_slot_is_refused_and_nothing_changes(self, cita, clinic_a, patient_a, service_a, prof, lunes, madrid):
        create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof,
            scheduled_at=at(lunes, 12, madrid), require_online_booking=False, source=Appointment.Source.STAFF,
        )

        with pytest.raises(SlotUnavailable):
            update_appointment(cita, scheduled_at=at(lunes, 12, madrid))

        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 10, madrid)

    def test_it_does_not_collide_with_itself(self, cita, lunes, madrid, commits, queued):
        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 10, madrid, minute=15))

        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 10, madrid, minute=15)

    def test_outside_the_professionals_schedule_is_refused(self, cita, lunes, madrid):
        with pytest.raises(Exception) as error:
            update_appointment(cita, scheduled_at=at(lunes, 20, madrid))

        assert 'horario' in str(error.value.detail).lower() or 'disponible' in str(error.value.detail).lower()

    def test_no_minimum_notice_for_the_staff(self, cita, clinic_a, prof, lunes, madrid, commits, queued):
        """La antelación mínima es de la vía online; el staff encaja citas cuando quiere."""
        clinic_a.min_booking_notice_minutes = 100000
        clinic_a.save()

        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 11, madrid)

    @pytest.mark.parametrize('status', [
        Appointment.Status.CANCELLED, Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW,
    ])
    def test_a_closed_appointment_cannot_be_modified(self, cita, status):
        Appointment.objects.filter(pk=cita.pk).update(status=status)

        with pytest.raises(InvalidTransition):
            update_appointment(cita, notes='tarde')

    def test_it_works_on_the_current_state_not_on_a_stale_copy(self, cita, lunes, madrid):
        """Se cancela mientras el staff rellena el formulario: no se escribe encima."""
        stale = Appointment.objects.get(pk=cita.pk)
        cancel_appointment(cita)

        with pytest.raises(InvalidTransition):
            update_appointment(stale, scheduled_at=at(lunes, 11, madrid))

        cita.refresh_from_db()
        assert cita.status == Appointment.Status.CANCELLED and cita.scheduled_at == at(lunes, 10, madrid)


@pytest.mark.django_db
class TestNotifyingThePatient:
    def test_moving_a_confirmed_appointment_queues_the_change_notice_with_the_old_time(
        self, cita, lunes, madrid, commits, queued
    ):
        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        queued.assert_called_once()
        call = queued.call_args.kwargs
        assert call['kind'] == 'booking_changed'
        assert datetime.fromisoformat(call['previous_at']) == at(lunes, 10, madrid)
        assert datetime.fromisoformat(call['scheduled_at']) == at(lunes, 11, madrid)

    def test_the_staff_can_choose_not_to_notify(self, cita, lunes, madrid, commits, queued):
        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid), notify_patient=False)

        queued.assert_not_called()

    def test_a_pending_appointment_has_nothing_to_correct_so_nothing_is_sent(self, cita, lunes, madrid, commits, queued):
        cita.status = Appointment.Status.PENDING
        cita.save()

        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        queued.assert_not_called()

    def test_a_rescheduled_appointment_the_agent_moved_is_notified_when_the_clinic_moves_it_again(
        self, cita, lunes, madrid, commits, queued
    ):
        cita.status = Appointment.Status.RESCHEDULED
        cita.save()

        with commits():
            update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        queued.assert_called_once()

    def test_it_ends_as_a_whatsapp_message_with_the_new_and_old_time(
        self, cita, clinic_a, lunes, madrid, commits
    ):
        clinic_a.whatsapp_phone_number_id = '111'
        clinic_a.whatsapp_token = 'EAAG'
        clinic_a.save()
        WhatsAppTemplate.objects.create(
            clinic=clinic_a, name='cambio', body='Tu cita de las {{1}} pasa a las {{2}}.',
            status=WhatsAppTemplate.Status.APPROVED, meta_template_id='1', body_examples=['9:00', '10:00'],
            purpose=WhatsAppTemplate.Purpose.BOOKING_CHANGED,
            variable_map={'1': 'previous_time', '2': 'time'},
        )

        with patch('agent.services.send_template', side_effect=lambda *a: f'wamid.{uuid.uuid4().hex}') as meta:
            with commits():
                update_appointment(cita, scheduled_at=at(lunes, 11, madrid))

        assert meta.call_args.args[1] == PHONE
        assert meta.call_args.args[4] == ['10:00', '11:00']
        assert ChatMessage.objects.get(message_type='template').raw['kind'] == 'booking_changed'


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def _post(client, cita, madrid, **overrides):
    local = cita.scheduled_at.astimezone(madrid)
    data = {
        'service': cita.service_id, 'professional': cita.professional_id,
        'date': local.date().isoformat(), 'time': local.strftime('%H:%M'),
        'notes': cita.notes, 'notify_patient': 'on', **overrides,
    }
    return client.post(reverse('appointments:update', args=[cita.pk]), data)


@pytest.mark.django_db
class TestTheScreen:
    def test_the_form_comes_filled_with_the_current_data(self, client, admin_user, cita):
        client.force_login(admin_user)

        response = client.get(reverse('appointments:update', args=[cita.pk]))

        assert response.status_code == 200
        html = response.content.decode()
        assert 'value="10:00"' in html and f'value="{cita.professional_id}" selected' in html

    def test_moving_it_saves_redirects_and_says_the_patient_will_be_told(
        self, client, admin_user, cita, clinic_a, lunes, madrid, commits, queued
    ):
        clinic_a.whatsapp_phone_number_id, clinic_a.whatsapp_token = '111', 'EAAG'
        clinic_a.save()
        WhatsAppTemplate.objects.create(
            clinic=clinic_a, name='cambio', body='Nueva hora: {{1}}.', status=WhatsAppTemplate.Status.APPROVED,
            meta_template_id='1', body_examples=['10:00'], purpose=WhatsAppTemplate.Purpose.BOOKING_CHANGED,
            variable_map={'1': 'time'},
        )
        client.force_login(admin_user)

        with commits():
            response = _post(client, cita, madrid, time='11:30')

        assert response.status_code == 302
        assert response.url == reverse('core:dashboard-manage-appointment', args=[cita.pk])
        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 11, madrid, minute=30)
        message = [str(m) for m in response.wsgi_request._messages][0]
        assert 'Se avisará al paciente' in message
        queued.assert_called_once()

    def test_it_says_when_the_patient_will_not_be_told(
        self, client, admin_user, cita, clinic_a, madrid, commits, queued
    ):
        """La clínica no tiene plantilla de cambio: se guarda igual y se dice claro."""
        clinic_a.whatsapp_phone_number_id, clinic_a.whatsapp_token = '111', 'EAAG'
        clinic_a.save()
        client.force_login(admin_user)

        html = client.get(reverse('appointments:update', args=[cita.pk])).content.decode()
        assert 'No se avisará' in html and 'aviso de cambio de cita' in html

        with commits():
            response = _post(client, cita, madrid, time='11:30')
        message = [str(m) for m in response.wsgi_request._messages][0]
        assert 'No se ha avisado al paciente' in message

    def test_unticking_the_box_does_not_notify(self, client, admin_user, cita, madrid, commits, queued):
        client.force_login(admin_user)
        data = {
            'service': cita.service_id, 'professional': cita.professional_id,
            'date': cita.scheduled_at.astimezone(madrid).date().isoformat(), 'time': '11:30', 'notes': '',
        }

        with commits():
            client.post(reverse('appointments:update', args=[cita.pk]), data)

        queued.assert_not_called()

    def test_an_occupied_slot_comes_back_as_an_error_in_the_form(
        self, client, admin_user, cita, clinic_a, patient_a, service_a, prof, lunes, madrid
    ):
        create_appointment(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=prof,
            scheduled_at=at(lunes, 12, madrid), require_online_booking=False, source=Appointment.Source.STAFF,
        )
        client.force_login(admin_user)

        response = _post(client, cita, madrid, time='12:00')

        assert response.status_code == 200
        assert response.context['form'].non_field_errors()
        cita.refresh_from_db()
        assert cita.scheduled_at == at(lunes, 10, madrid)

    def test_a_past_time_is_refused_but_an_unmoved_one_can_still_be_edited(
        self, client, admin_user, cita, madrid
    ):
        client.force_login(admin_user)
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()

        assert _post(client, cita, madrid, date=yesterday).status_code == 200

        Appointment.objects.filter(pk=cita.pk).update(scheduled_at=timezone.now() - timedelta(hours=1))
        cita.refresh_from_db()
        response = _post(client, cita, madrid, notes='Ya pasó, pero corrijo la nota')
        assert response.status_code == 302

    def test_resubmitting_the_same_time_is_not_a_move_even_if_the_appointment_has_seconds(
        self, client, admin_user, cita, madrid, commits, queued
    ):
        """El formulario enseña HH:MM; reenviarlo tal cual no puede avisar de un cambio de hora."""
        Appointment.objects.filter(pk=cita.pk).update(scheduled_at=cita.scheduled_at + timedelta(seconds=37))
        cita.refresh_from_db()
        client.force_login(admin_user)

        with commits():
            response = _post(client, cita, madrid, notes='Solo una nota')

        assert response.status_code == 302
        before = cita.scheduled_at
        cita.refresh_from_db()
        assert cita.scheduled_at == before and cita.notes == 'Solo una nota'
        queued.assert_not_called()

    def test_staff_can_use_it_too(self, client, staff_user, cita):
        client.force_login(staff_user)

        assert client.get(reverse('appointments:update', args=[cita.pk])).status_code == 200

    def test_another_clinics_appointment_is_a_404(self, client, admin_user_b, cita):
        client.force_login(admin_user_b)

        assert client.get(reverse('appointments:update', args=[cita.pk])).status_code == 404
        assert client.post(reverse('appointments:update', args=[cita.pk]), {}).status_code == 404

    def test_anonymous_goes_to_login(self, client, cita):
        response = client.get(reverse('appointments:update', args=[cita.pk]))

        assert response.status_code == 302 and '/login/' in response.url

    def test_a_closed_appointment_redirects_to_its_page(self, client, admin_user, cita):
        Appointment.objects.filter(pk=cita.pk).update(status=Appointment.Status.CANCELLED)
        client.force_login(admin_user)

        response = client.get(reverse('appointments:update', args=[cita.pk]))

        assert response.status_code == 302
        assert response.url == reverse('core:dashboard-manage-appointment', args=[cita.pk])

    def test_the_manage_page_offers_the_button_only_while_it_makes_sense(self, client, admin_user, cita):
        client.force_login(admin_user)
        url = reverse('core:dashboard-manage-appointment', args=[cita.pk])
        edit = reverse('appointments:update', args=[cita.pk])

        assert edit in client.get(url).content.decode()

        for status in (Appointment.Status.CANCELLED, Appointment.Status.COMPLETED):
            Appointment.objects.filter(pk=cita.pk).update(status=status)
            assert edit not in client.get(url).content.decode()

    def test_the_change_is_in_the_audit_trail(self, client, admin_user, cita, madrid, commits, queued):
        from audit.models import ChangeLog

        client.force_login(admin_user)
        before = ChangeLog.objects.count()

        with commits():
            _post(client, cita, madrid, time='11:30')

        assert ChangeLog.objects.count() > before
