"""Ausencias: quitar una guardada se puede deshacer, y la lista de motivos.

El patrón es el mismo que el de los tramos del horario semanal: la X no borra de
golpe una ausencia ya guardada, la deja atenuada con «Se eliminará al guardar» y
un «Deshacer». El borrado real ocurre al guardar.
"""
import re
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from appointments.forms import ProfessionalTimeOffForm, TimeOffFormSet
from appointments.models import ProfessionalTimeOff

SCHEDULE_URL = '/cuenta/horario/'
UTC = dt_timezone.utc


@pytest.fixture
def professional(staff_user):
    return staff_user.professional_profile


@pytest.fixture
def vacation(professional):
    return ProfessionalTimeOff.objects.create(
        professional=professional,
        starts_at=datetime(2026, 7, 1, 7, 0, tzinfo=UTC),    # 09:00 en Madrid (verano)
        ends_at=datetime(2026, 7, 15, 18, 0, tzinfo=UTC),    # 20:00
        reason=ProfessionalTimeOff.Reason.VACATION,
    )


def _management(**overrides):
    data = {
        'schedules-TOTAL_FORMS': '0', 'schedules-INITIAL_FORMS': '0',
        'schedules-MIN_NUM_FORMS': '0', 'schedules-MAX_NUM_FORMS': '1000',
        'timeoff-TOTAL_FORMS': '0', 'timeoff-INITIAL_FORMS': '0',
        'timeoff-MIN_NUM_FORMS': '0', 'timeoff-MAX_NUM_FORMS': '1000',
    }
    data.update(overrides)
    return data


def _post_removing(vacation, professional, **extra):
    return _management(**{
        'timeoff-TOTAL_FORMS': '1', 'timeoff-INITIAL_FORMS': '1',
        'timeoff-0-id': str(vacation.pk), 'timeoff-0-professional': str(professional.pk),
        'timeoff-0-starts_at': '2026-07-01T09:00', 'timeoff-0-ends_at': '2026-07-15T20:00',
        'timeoff-0-reason': 'vacation', 'timeoff-0-note': '', 'timeoff-0-DELETE': 'on',
        **extra,
    })


def _timeoff_section(html):
    start = html.index('data-formset="timeoff"')
    return html[start:html.index('</section>', start)]


# ---------------------------------------------------------------------------
# Los motivos
# ---------------------------------------------------------------------------

class TestReasons:
    def test_the_list_keeps_the_old_reasons_and_adds_new_ones(self):
        reasons = dict(ProfessionalTimeOff.Reason.choices)

        for old in ('vacation', 'sick_leave', 'training', 'other'):
            assert old in reasons
        assert reasons['congress'] == 'Congreso'
        assert reasons['meeting'] == 'Reunión'
        assert reasons['home_visits'] == 'Visitas a domicilio'
        assert reasons['personal'] == 'Asuntos propios'
        assert reasons['medical_appt'] == 'Cita médica'
        assert reasons['parental_leave'] == 'Maternidad o paternidad'
        assert reasons['holiday'] == 'Festivo'

    def test_other_stays_the_last_and_the_default(self):
        values = [value for value, _ in ProfessionalTimeOff.Reason.choices]

        assert values[-1] == 'other'
        assert ProfessionalTimeOff._meta.get_field('reason').default == 'other'

    def test_every_key_fits_the_column(self):
        limit = ProfessionalTimeOff._meta.get_field('reason').max_length

        assert all(len(value) <= limit for value, _ in ProfessionalTimeOff.Reason.choices)

    @pytest.mark.django_db
    def test_the_select_offers_every_reason(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(SCHEDULE_URL).content.decode()
        template = html[html.index('data-formset-empty', html.index('data-formset="timeoff"')):]

        for value, label in ProfessionalTimeOff.Reason.choices:
            assert f'<option value="{value}"' in template
            assert label in template

    @pytest.mark.django_db
    @pytest.mark.parametrize('value', ['congress', 'meeting', 'home_visits', 'personal',
                                       'medical_appt', 'parental_leave', 'holiday'])
    def test_a_new_reason_can_be_saved(self, client, staff_user, professional, value):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-09-01T09:00', 'timeoff-0-ends_at': '2026-09-01T14:00',
            'timeoff-0-reason': value, 'timeoff-0-note': '',
        }))

        assert response.status_code == 302
        assert ProfessionalTimeOff.objects.get(professional=professional).reason == value

    @pytest.mark.django_db
    def test_a_reason_that_does_not_exist_is_rejected(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-09-01T09:00', 'timeoff-0-ends_at': '2026-09-01T14:00',
            'timeoff-0-reason': 'inventado',
        }))

        assert response.status_code == 200
        assert ProfessionalTimeOff.objects.count() == 0


# ---------------------------------------------------------------------------
# Quitar y deshacer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRemovingAndUndoing:
    def test_every_saved_absence_carries_its_undo(self, client, staff_user, vacation):
        client.force_login(staff_user)
        section = _timeoff_section(client.get(SCHEDULE_URL).content.decode())

        assert section.count('data-removed-note') >= 1
        assert section.count('data-formset-undo') >= 1
        assert 'data-row-body' in section
        assert ' data-removed' not in re.sub(r'data-removed-(note|label)', '', section)

    def test_a_removed_saved_absence_comes_back_dimmed_after_a_rejected_save(
        self, client, staff_user, professional, vacation
    ):
        """Quitarla y que el envío falle no puede perderla: se ve y se recupera."""
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _post_removing(
            vacation, professional,
            **{'schedules-TOTAL_FORMS': '1', 'schedules-0-day_of_week': '0',
               'schedules-0-start_time': '15:00', 'schedules-0-end_time': '09:00',
               'schedules-0-is_active': 'on'},  # un tramo inválido tumba el envío
        ))

        assert response.status_code == 200
        section = _timeoff_section(response.content.decode())
        row = re.search(r'<div class="(formset-row[^"]*)"([^>]*)>', section)
        assert 'hidden' not in row.group(1) and 'data-removed' in row.group(2)
        assert '01/07/2026 09:00 – 15/07/2026 20:00 · Vacaciones' in section   # hora local
        assert 'Se eliminará al guardar' in section
        assert 'hidden' in re.search(r'<div data-row-body([^>]*)>', section).group(1)
        assert ProfessionalTimeOff.objects.filter(pk=vacation.pk).exists()

    def test_a_removed_new_absence_is_just_hidden(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-09-01T09:00', 'timeoff-0-ends_at': '2026-09-01T14:00',
            'timeoff-0-reason': 'other', 'timeoff-0-DELETE': 'on',
            'schedules-TOTAL_FORMS': '1', 'schedules-0-day_of_week': '0',
            'schedules-0-start_time': '15:00', 'schedules-0-end_time': '09:00',
            'schedules-0-is_active': 'on',
        }))

        section = _timeoff_section(response.content.decode())
        row = re.search(r'<div class="(formset-row[^"]*)"([^>]*)>', section)
        assert 'hidden' in row.group(1) and 'data-removed' not in row.group(2)

    def test_removing_and_saving_does_delete_it(self, client, staff_user, professional, vacation):
        client.force_login(staff_user)

        response = client.post(SCHEDULE_URL, _post_removing(vacation, professional))

        assert response.status_code == 302
        assert not ProfessionalTimeOff.objects.filter(pk=vacation.pk).exists()

    def test_undoing_before_saving_keeps_it(self, client, staff_user, professional, vacation):
        """Quitarla y deshacer es no tocarla: el envío no trae `DELETE`."""
        client.force_login(staff_user)
        data = _post_removing(vacation, professional)
        del data['timeoff-0-DELETE']

        response = client.post(SCHEDULE_URL, data)

        assert response.status_code == 302
        assert ProfessionalTimeOff.objects.filter(pk=vacation.pk).exists()


# ---------------------------------------------------------------------------
# El formulario
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFormState:
    def test_a_fresh_form_is_not_removed(self, professional, vacation):
        formset = TimeOffFormSet(instance=professional, prefix='timeoff')

        form = formset.forms[0]
        assert not form.is_marked_for_deletion
        assert not form.removed_existing and not form.removed_new

    def test_the_summary_is_in_the_clinics_time_not_utc(self, professional, vacation, clinic_a):
        form = ProfessionalTimeOffForm(
            instance=vacation, clinic_tz=ZoneInfo(clinic_a.timezone), prefix='timeoff-0',
        )

        assert form.removal_summary == '01/07/2026 09:00 – 15/07/2026 20:00 · Vacaciones'

    def test_errors_read_in_spanish(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'timeoff-TOTAL_FORMS': '1',
            'timeoff-0-starts_at': '2026-09-02T09:00', 'timeoff-0-ends_at': '2026-09-01T14:00',
            'timeoff-0-reason': 'other',
        }))

        html = response.content.decode()
        assert response.status_code == 200
        assert 'Hasta:' in html and 'Ends at' not in html and 'Starts at' not in html
