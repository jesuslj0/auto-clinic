"""Abrir la visita de una cita (`appointments:visit-open`): la puerta de la agenda a la historia.

Defiende que:

1. La visita queda **enganchada a la cita**, con su profesional y su hora, y se va
   derecho a escribir la nota.
2. **No se duplica**: abrir dos veces, o abrir tras registrar un procedimiento,
   reutiliza la misma visita.
3. Una cita sin ficha o cancelada no llega a la historia.
4. Aislamiento por clínica, lectura con `AccessLog`, y la pantalla de la cita no
   enseña nada clínico (solo si hay visita).
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from audit.models import AccessLog
from clinical.models import ClinicalNote, Episode, PerformedProcedure, Visit


def open_url(appointment):
    return reverse('appointments:visit-open', kwargs={'appointment_id': appointment.pk})


def manage_url(appointment):
    return reverse('core:dashboard-manage-appointment', kwargs={'appointment_id': appointment.pk})


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
class TestOpening:
    def test_the_page_offers_the_open_episode_preselected(self, logged, appointment_a, episode_a):
        response = logged.get(open_url(appointment_a))
        html = response.content.decode()

        assert response.status_code == 200
        assert 'Dolor en el talón derecho' in html
        assert f'value="{episode_a.pk}" selected' in html

    def test_opens_a_visit_hooked_to_the_appointment(self, logged, appointment_a, episode_a, patient_a):
        response = logged.post(open_url(appointment_a), {'episode': episode_a.pk})

        visit = Visit.objects.get(appointment=appointment_a)
        assert visit.episode == episode_a
        assert visit.professional == appointment_a.professional
        assert visit.occurred_at == appointment_a.scheduled_at
        assert response['Location'] == reverse(
            'patients:note-create', kwargs={'id': patient_a.pk, 'visit_id': visit.pk}
        )

    def test_opens_a_new_episode_when_none_is_chosen(self, logged, appointment_a, patient_a):
        logged.post(open_url(appointment_a), {'episode_reason': 'Revisión de plantillas'})

        visit = Visit.objects.get(appointment=appointment_a)
        assert visit.episode.reason == 'Revisión de plantillas'
        assert visit.episode.history.patient == patient_a

    def test_needs_an_episode_or_a_reason(self, logged, appointment_a):
        response = logged.post(open_url(appointment_a), {})

        assert response.status_code == 200
        assert not Visit.objects.filter(appointment=appointment_a).exists()

    def test_a_second_open_goes_to_the_same_visit_without_duplicating(
        self, logged, appointment_a, episode_a, patient_a
    ):
        logged.post(open_url(appointment_a), {'episode': episode_a.pk})
        response = logged.get(open_url(appointment_a))

        assert Visit.objects.filter(appointment=appointment_a).count() == 1
        assert response.status_code == 302
        assert '/notas/nueva/' in response['Location']

    def test_it_resumes_the_pending_draft(self, logged, appointment_a, episode_a, patient_a):
        logged.post(open_url(appointment_a), {'episode': episode_a.pk})
        visit = Visit.objects.get(appointment=appointment_a)
        draft = ClinicalNote.objects.create(visit=visit, subjective='Algo')

        response = logged.get(open_url(appointment_a))

        assert response['Location'] == reverse(
            'patients:note-edit', kwargs={'id': patient_a.pk, 'note_id': draft.pk}
        )

    def test_shares_the_visit_with_the_procedure_flow(
        self, logged, appointment_a, episode_a, service_a
    ):
        """Abrir la visita y registrar un procedimiento después no crea dos visitas."""
        logged.post(open_url(appointment_a), {'episode': episode_a.pk})
        logged.post(
            reverse('appointments:procedure-create', kwargs={'appointment_id': appointment_a.pk}),
            {'service': service_a.pk, 'performed_on': timezone.localdate().isoformat()},
        )

        assert Visit.objects.filter(appointment=appointment_a).count() == 1
        procedure = PerformedProcedure.objects.get()
        assert procedure.visit.appointment == appointment_a


@pytest.mark.django_db
class TestRefusals:
    def test_a_cancelled_appointment_has_no_visit(self, logged, appointment_a, episode_a):
        appointment_a.status = Appointment.Status.CANCELLED
        appointment_a.save()

        response = logged.post(open_url(appointment_a), {'episode': episode_a.pk})

        assert response['Location'] == manage_url(appointment_a)
        assert not Visit.objects.exists()

    def test_an_appointment_without_a_patient_file_has_no_visit(self, logged, appointment_a):
        appointment_a.patient = None
        appointment_a.patient_name = 'Sin ficha'
        appointment_a.patient_phone = '+34600000000'
        appointment_a.save()

        response = logged.get(open_url(appointment_a))

        assert response['Location'] == manage_url(appointment_a)
        assert not Visit.objects.exists()

    def test_another_clinics_appointment_is_denied(self, logged, appointment_b):
        assert logged.get(open_url(appointment_b)).status_code == 403
        assert logged.post(open_url(appointment_b), {'episode_reason': 'x'}).status_code == 403
        assert not Visit.objects.exists()

    def test_anonymous_goes_to_login(self, client, appointment_a):
        response = client.get(open_url(appointment_a))

        assert response.status_code == 302 and 'login' in response['Location']


@pytest.mark.django_db
class TestAuditAndScreen:
    def test_the_read_is_logged_against_the_patient(self, logged, appointment_a, patient_a):
        logged.get(open_url(appointment_a))

        assert AccessLog.objects.filter(patient=patient_a).exists()

    def test_the_appointment_screen_links_to_the_visit(self, logged, appointment_a):
        html = logged.get(manage_url(appointment_a)).content.decode()

        assert open_url(appointment_a) in html
        assert 'Abrir visita' in html

    def test_it_says_when_the_visit_is_already_open_and_shows_nothing_clinical(
        self, logged, appointment_a, episode_a
    ):
        logged.post(open_url(appointment_a), {'episode': episode_a.pk})
        visit = Visit.objects.get(appointment=appointment_a)
        ClinicalNote.objects.create(visit=visit, subjective='Texto clínico reservado')

        html = logged.get(manage_url(appointment_a)).content.decode()

        assert 'Ir a la nota' in html
        assert 'Texto clínico reservado' not in html
        assert 'Dolor en el talón derecho' not in html
