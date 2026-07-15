"""Horario y ausencias editables desde el edit del profesional (inline formsets).

Dos cosas que merecen test propio: que el horario (hora local, sin conversión) se
guarda tal cual, y que las ausencias (instantes UTC) hacen el viaje local→UTC al
guardar y UTC→local al mostrar. Esa conversión es la única parte con trampa.
"""
from datetime import time
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from appointments.models import Professional, ProfessionalSchedule, ProfessionalTimeOff
from core.models import User


@pytest.fixture
def prof_sin_horario(db, clinic_a, service_a):
    """Un profesional recién creado: sin tramos ni ausencias todavía."""
    user = User.objects.create_user(
        email='nuevo@alpha.test', password='x', clinic=clinic_a, role=User.Role.ADMIN,
    )
    professional = user.professional_profile
    professional.services.add(service_a)
    return professional


@pytest.fixture
def edit_client(client, prof_sin_horario):
    client.force_login(prof_sin_horario.user)
    return client


def _url(professional):
    return reverse('appointments:professionals-edit', args=[professional.pk])


def _base_post(professional, **overrides):
    """Form principal + management forms vacíos de ambos formsets."""
    data = {
        'user': professional.user_id,
        'professional_type': professional.professional_type,
        'services': list(professional.services.values_list('pk', flat=True)),
        'schedules-TOTAL_FORMS': '0',
        'schedules-INITIAL_FORMS': '0',
        'schedules-MIN_NUM_FORMS': '0',
        'schedules-MAX_NUM_FORMS': '1000',
        'timeoff-TOTAL_FORMS': '0',
        'timeoff-INITIAL_FORMS': '0',
        'timeoff-MIN_NUM_FORMS': '0',
        'timeoff-MAX_NUM_FORMS': '1000',
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRender:
    def test_edit_shows_both_formsets(self, edit_client, prof_sin_horario):
        resp = edit_client.get(_url(prof_sin_horario))
        assert resp.status_code == 200
        assert 'schedule_formset' in resp.context
        assert 'timeoff_formset' in resp.context

    def test_create_view_does_not_show_formsets(self, edit_client):
        """Los formsets viven solo en el edit: en el alta aún no hay clínica fijada."""
        resp = edit_client.get(reverse('appointments:professionals-create'))
        assert resp.status_code == 200
        assert 'schedule_formset' not in resp.context


# ---------------------------------------------------------------------------
# Horario (hora local, sin conversión)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSchedule:
    def test_adds_a_schedule_slot(self, edit_client, prof_sin_horario):
        data = _base_post(prof_sin_horario, **{
            'schedules-TOTAL_FORMS': '1',
            'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00',
            'schedules-0-is_active': 'on',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 302

        tramo = ProfessionalSchedule.objects.get(professional=prof_sin_horario)
        assert tramo.day_of_week == 0
        assert tramo.start_time == time(9, 0)
        assert tramo.end_time == time(14, 0)

    def test_overlapping_new_slots_are_rejected(self, edit_client, prof_sin_horario):
        """Dos tramos que se pisan el mismo día: el formset lo caza aunque ninguno
        esté aún en BD."""
        data = _base_post(prof_sin_horario, **{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00',
            'schedules-0-is_active': 'on',
            'schedules-1-day_of_week': '0',
            'schedules-1-start_time': '13:00',
            'schedules-1-end_time': '18:00',
            'schedules-1-is_active': 'on',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 200  # se repinta con el error
        assert ProfessionalSchedule.objects.count() == 0

    def test_end_before_start_is_rejected(self, edit_client, prof_sin_horario):
        data = _base_post(prof_sin_horario, **{
            'schedules-TOTAL_FORMS': '1',
            'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '14:00',
            'schedules-0-end_time': '09:00',
            'schedules-0-is_active': 'on',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 200
        assert ProfessionalSchedule.objects.count() == 0

    def test_split_shift_same_day_is_allowed(self, edit_client, prof_sin_horario):
        """Jornada partida: dos tramos disjuntos el mismo día sí valen."""
        data = _base_post(prof_sin_horario, **{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00',
            'schedules-0-is_active': 'on',
            'schedules-1-day_of_week': '0',
            'schedules-1-start_time': '16:00',
            'schedules-1-end_time': '20:00',
            'schedules-1-is_active': 'on',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 302
        assert ProfessionalSchedule.objects.filter(professional=prof_sin_horario).count() == 2

    def test_delete_removes_existing_slot(self, edit_client, prof_sin_horario):
        tramo = ProfessionalSchedule.objects.create(
            professional=prof_sin_horario, day_of_week=0,
            start_time=time(9, 0), end_time=time(14, 0),
        )
        data = _base_post(prof_sin_horario, **{
            'schedules-TOTAL_FORMS': '1',
            'schedules-INITIAL_FORMS': '1',
            'schedules-0-id': str(tramo.pk),
            'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00',
            'schedules-0-is_active': 'on',
            'schedules-0-DELETE': 'on',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 302
        assert not ProfessionalSchedule.objects.filter(pk=tramo.pk).exists()


# ---------------------------------------------------------------------------
# Ausencias (instantes UTC tecleados en hora local de la clínica)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTimeOff:
    def test_local_input_is_stored_as_utc(self, edit_client, prof_sin_horario, clinic_a):
        """El staff teclea hora de Madrid; la BD guarda UTC.

        10 de agosto, 09:00 en Madrid (UTC+2 en verano) → 07:00 UTC.
        """
        data = _base_post(prof_sin_horario, **{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-08-10T09:00',
            'timeoff-0-ends_at': '2026-08-10T13:00',
            'timeoff-0-reason': ProfessionalTimeOff.Reason.VACATION,
            'timeoff-0-note': 'Congreso',
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 302

        ausencia = ProfessionalTimeOff.objects.get(professional=prof_sin_horario)
        assert ausencia.starts_at.hour == 7  # UTC
        assert ausencia.starts_at.astimezone(ZoneInfo(clinic_a.timezone)).hour == 9  # local

    def test_existing_timeoff_prefilled_in_local_time(self, edit_client, prof_sin_horario):
        """Al editar, el input muestra hora local, no el UTC crudo de la BD."""
        from django.utils import timezone as djtz

        ausencia = ProfessionalTimeOff.objects.create(
            professional=prof_sin_horario,
            starts_at=djtz.datetime(2026, 8, 10, 7, 0, tzinfo=ZoneInfo('UTC')),
            ends_at=djtz.datetime(2026, 8, 10, 11, 0, tzinfo=ZoneInfo('UTC')),
            reason=ProfessionalTimeOff.Reason.VACATION,
        )
        resp = edit_client.get(_url(prof_sin_horario))
        formset = resp.context['timeoff_formset']
        form = next(f for f in formset.forms if f.instance.pk == ausencia.pk)
        # 07:00 UTC → 09:00 Madrid
        assert form['starts_at'].value() == '2026-08-10T09:00'

    def test_end_before_start_is_rejected(self, edit_client, prof_sin_horario):
        data = _base_post(prof_sin_horario, **{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-08-10T13:00',
            'timeoff-0-ends_at': '2026-08-10T09:00',
            'timeoff-0-reason': ProfessionalTimeOff.Reason.OTHER,
        })
        resp = edit_client.post(_url(prof_sin_horario), data)
        assert resp.status_code == 200
        assert ProfessionalTimeOff.objects.count() == 0
