"""El horario semanal se pinta agrupado por días, con los tramos de cada uno juntos.

Solo es presentación: el formset sigue siendo la lista plana `schedules-<n>-…` de
siempre, y se guarda y se valida igual. Lo que se vigila aquí es que cada tramo
caiga en el bloque de su día —también al repintar un formulario con errores—, que
el día viaje en el propio tramo, y que nada se pierda en silencio.
"""
import re
from datetime import time

import pytest
from django.urls import reverse

from appointments.forms import ScheduleFormSet
from appointments.models import ProfessionalSchedule

SCHEDULE_URL = '/cuenta/horario/'

MONDAY, TUESDAY, WEDNESDAY = 0, 1, 2


def _tramo(professional, day, start, end, **extra):
    return ProfessionalSchedule.objects.create(
        professional=professional, day_of_week=day,
        start_time=start, end_time=end, **extra,
    )


@pytest.fixture
def professional(staff_user):
    return staff_user.professional_profile


@pytest.fixture
def two_a_day(professional):
    """Lunes con jornada partida y martes solo de mañana."""
    _tramo(professional, MONDAY, time(9, 0), time(14, 0))
    _tramo(professional, MONDAY, time(16, 0), time(20, 0))
    _tramo(professional, TUESDAY, time(9, 0), time(13, 0))


def _management(**overrides):
    data = {
        'schedules-TOTAL_FORMS': '0', 'schedules-INITIAL_FORMS': '0',
        'schedules-MIN_NUM_FORMS': '0', 'schedules-MAX_NUM_FORMS': '1000',
        'timeoff-TOTAL_FORMS': '0', 'timeoff-INITIAL_FORMS': '0',
        'timeoff-MIN_NUM_FORMS': '0', 'timeoff-MAX_NUM_FORMS': '1000',
    }
    data.update(overrides)
    return data


def _day_block(html, day):
    """El HTML del bloque de un día: de su `data-day` al del siguiente día."""
    start = html.index(f'data-day="{day}"')
    following = html.find('data-day="', start + 1)
    return html[start:following if following != -1 else len(html)]


# ---------------------------------------------------------------------------
# El agrupado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGroupingByDay:
    def test_there_are_always_seven_days_monday_first(self, professional):
        formset = ScheduleFormSet(instance=professional, prefix='schedules')

        assert [day['value'] for day in formset.week] == [0, 1, 2, 3, 4, 5, 6]
        assert [day['label'] for day in formset.week][:2] == ['Lunes', 'Martes']
        assert all(day['forms'] == [] and day['live'] == 0 for day in formset.week)

    def test_each_tramo_lands_in_its_day(self, professional, two_a_day):
        week = ScheduleFormSet(instance=professional, prefix='schedules').week

        assert [len(day['forms']) for day in week] == [2, 1, 0, 0, 0, 0, 0]
        assert [f.instance.start_time for f in week[0]['forms']] == [time(9, 0), time(16, 0)]

    def test_a_submitted_form_is_grouped_by_what_was_sent(self, professional):
        """Al repintar con errores manda lo enviado, no lo que hubiera guardado."""
        data = _management(**{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '4', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-is_active': 'on',
            'schedules-1-day_of_week': '4', 'schedules-1-start_time': '16:00',
            'schedules-1-end_time': '20:00', 'schedules-1-is_active': 'on',
        })
        week = ScheduleFormSet(data, instance=professional, prefix='schedules').week

        assert [len(day['forms']) for day in week] == [0, 0, 0, 0, 2, 0, 0]

    def test_a_row_marked_for_deletion_does_not_count_as_live(self, professional):
        tramo = _tramo(professional, MONDAY, time(9, 0), time(14, 0))
        data = _management(**{
            'schedules-TOTAL_FORMS': '1', 'schedules-INITIAL_FORMS': '1',
            'schedules-0-id': str(tramo.pk), 'schedules-0-professional': str(professional.pk),
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-DELETE': 'on',
        })
        monday = ScheduleFormSet(data, instance=professional, prefix='schedules').week[0]

        assert len(monday['forms']) == 1 and monday['live'] == 0

    def test_a_row_without_a_valid_day_is_not_lost(self, professional):
        data = _management(**{
            'schedules-TOTAL_FORMS': '1',
            'schedules-0-day_of_week': '9', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-is_active': 'on',
        })
        formset = ScheduleFormSet(data, instance=professional, prefix='schedules')

        assert all(day['forms'] == [] for day in formset.week)
        assert len(formset.stray_forms) == 1
        assert not formset.is_valid()

    def test_the_overlap_rule_still_applies_to_the_same_day(self, professional):
        data = _management(**{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-is_active': 'on',
            'schedules-1-day_of_week': '0', 'schedules-1-start_time': '13:00',
            'schedules-1-end_time': '18:00', 'schedules-1-is_active': 'on',
        })

        assert not ScheduleFormSet(data, instance=professional, prefix='schedules').is_valid()


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheScreen:
    def _html(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(SCHEDULE_URL)
        assert response.status_code == 200
        return response.content.decode()

    def test_there_is_a_block_per_day(self, client, staff_user):
        html = self._html(client, staff_user)

        assert [int(d) for d in re.findall(r'data-day="(\d)"', html)] == [0, 1, 2, 3, 4, 5, 6]
        for label in ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'):
            assert f'>{label}</h4>' in html

    def test_a_days_tramos_sit_in_its_block(self, client, staff_user, two_a_day):
        html = self._html(client, staff_user)

        monday, tuesday, wednesday = (_day_block(html, d) for d in (0, 1, 2))
        assert monday.count('class="formset-row') == 2
        assert 'value="09:00"' in monday and 'value="16:00"' in monday
        assert tuesday.count('class="formset-row') == 1
        assert 'value="13:00"' in tuesday
        assert wednesday.count('class="formset-row') == 0

    def test_tramos_are_side_by_side(self, client, staff_user, two_a_day):
        monday = _day_block(self._html(client, staff_user), 0)

        assert 'data-day-slots class="grid grid-cols-1 gap-3 sm:grid-cols-2"' in monday

    def test_the_day_travels_in_a_hidden_field_and_is_not_asked_again(
        self, client, staff_user, two_a_day
    ):
        monday = _day_block(self._html(client, staff_user), 0)

        assert monday.count('type="hidden" name="schedules-0-day_of_week"') == 1
        assert re.search(r'name="schedules-0-day_of_week"[^>]*value="0"', monday)
        assert re.search(r'name="schedules-1-day_of_week"[^>]*value="0"', monday)
        assert '<select' not in monday

    def test_empty_days_say_so_and_days_with_tramos_do_not(self, client, staff_user, two_a_day):
        html = self._html(client, staff_user)

        wednesday = _day_block(html, 2)
        monday = _day_block(html, 0)
        assert re.search(r'data-day-closed class="[^"]*"', wednesday)
        assert 'hidden' not in re.search(r'data-day-closed class="([^"]*)"', wednesday).group(1)
        assert 'hidden' in re.search(r'data-day-closed class="([^"]*)"', monday).group(1)

    def test_the_template_for_new_tramos_has_an_empty_day_to_fill_in(self, client, staff_user):
        html = self._html(client, staff_user)
        template = html[html.index('<template data-formset-empty>'):]
        template = template[:template.index('</template>')]

        assert 'name="schedules-__prefix__-day_of_week"' in template
        assert re.search(r'name="schedules-__prefix__-day_of_week"[^>]*value=""', template)

    def test_every_day_has_its_own_add_button(self, client, staff_user):
        html = self._html(client, staff_user)

        for day in range(7):
            assert 'data-formset-add' in _day_block(html, day)

    def test_a_rejected_save_repaints_the_tramo_in_its_day_with_the_error(
        self, client, staff_user
    ):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '1',
            'schedules-0-day_of_week': '4', 'schedules-0-start_time': '15:00',
            'schedules-0-end_time': '09:00', 'schedules-0-is_active': 'on',
        }))

        assert response.status_code == 200
        friday = _day_block(response.content.decode(), 4)
        assert 'value="15:00"' in friday
        assert 'posterior' in friday  # el error del tramo se lee en su día
        assert 'Hasta:' in friday and 'End time' not in friday  # y con etiquetas en español
        assert ProfessionalSchedule.objects.count() == 0

    def test_a_saved_tramo_can_be_removed_and_brought_back(self, client, staff_user, two_a_day):
        """La X no lo hace desaparecer: cada tramo guardado trae su «Deshacer»."""
        html = self._html(client, staff_user)
        monday = _day_block(html, 0)

        assert monday.count('data-removed-note') == 2
        assert monday.count('data-formset-undo') == 2
        assert monday.count('data-row-body') == 2
        # En pie, el aviso de «se eliminará» está oculto y el cuerpo a la vista.
        assert 'data-removed' not in re.sub(r'data-removed-(note|label)', '', monday)

    def test_a_removed_saved_tramo_comes_back_dimmed_after_a_rejected_save(
        self, client, staff_user, professional
    ):
        """Quitar uno y que el envío falle no puede perder el tramo: se ve y se recupera."""
        tramo = _tramo(professional, MONDAY, time(9, 0), time(14, 0))
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '2', 'schedules-INITIAL_FORMS': '1',
            'schedules-0-id': str(tramo.pk), 'schedules-0-professional': str(professional.pk),
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-DELETE': 'on',
            'schedules-1-day_of_week': '1', 'schedules-1-start_time': '15:00',
            'schedules-1-end_time': '09:00', 'schedules-1-is_active': 'on',  # inválido
        }))

        monday = _day_block(response.content.decode(), 0)
        row = re.search(r'<div class="(formset-row[^"]*)"([^>]*)>', monday)
        assert 'hidden' not in row.group(1)            # sigue a la vista…
        assert 'data-removed' in row.group(2)          # …marcado como quitado
        assert '09:00 – 14:00' in monday               # con lo que se pierde
        assert 'Se eliminará al guardar' in monday
        body = re.search(r'<div data-row-body([^>]*)>', monday).group(1)
        assert 'hidden' in body                        # el cuerpo editable, oculto
        note = re.search(r'<div data-removed-note class="([^"]*)"', monday).group(1)
        assert not note.startswith('hidden')           # el aviso, visible
        closed = re.search(r'data-day-closed class="([^"]*)"', monday).group(1)
        assert 'hidden' not in closed                  # y el día cuenta como sin atención
        assert ProfessionalSchedule.objects.filter(pk=tramo.pk).exists()  # nada se guardó

    def test_a_removed_new_tramo_is_just_hidden(self, client, staff_user):
        """Uno que nunca se guardó no tiene nada que recuperar."""
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-DELETE': 'on',
            'schedules-1-day_of_week': '1', 'schedules-1-start_time': '15:00',
            'schedules-1-end_time': '09:00', 'schedules-1-is_active': 'on',  # inválido
        }))

        monday = _day_block(response.content.decode(), 0)
        row = re.search(r'<div class="(formset-row[^"]*)"([^>]*)>', monday)
        assert 'hidden' in row.group(1)
        assert 'data-removed' not in row.group(2)

    def test_undoing_a_removal_before_saving_keeps_the_tramo(self, client, staff_user, professional):
        """Quitarlo y deshacer es lo mismo que no tocarlo: el envío no trae `DELETE`."""
        tramo = _tramo(professional, MONDAY, time(9, 0), time(14, 0))
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '1', 'schedules-INITIAL_FORMS': '1',
            'schedules-0-id': str(tramo.pk), 'schedules-0-professional': str(professional.pk),
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-is_active': 'on',
        }))

        assert response.status_code == 302
        assert ProfessionalSchedule.objects.filter(pk=tramo.pk).exists()

    def test_removing_and_saving_does_delete_it(self, client, staff_user, professional):
        tramo = _tramo(professional, MONDAY, time(9, 0), time(14, 0))
        client.force_login(staff_user)
        client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '1', 'schedules-INITIAL_FORMS': '1',
            'schedules-0-id': str(tramo.pk), 'schedules-0-professional': str(professional.pk),
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-DELETE': 'on',
        }))

        assert not ProfessionalSchedule.objects.filter(pk=tramo.pk).exists()

    def test_a_cancel_link_reloads_the_saved_schedule(self, client, staff_user):
        html = self._html(client, staff_user)
        link = re.search(r'<a href="/cuenta/horario/"[^>]*>Cancelar</a>', html, re.S)

        assert link, 'no hay enlace Cancelar'
        assert 'x-show="dirty"' in link.group(0)          # solo cuando hay algo que descartar
        assert 'hx-target="#account-tab-region"' in link.group(0)

    def test_cancel_discards_everything_unsaved(self, client, staff_user, professional):
        """Cancelar es volver a pedir la pestaña: lo no guardado no existe para el servidor."""
        tramo = _tramo(professional, MONDAY, time(9, 0), time(14, 0))
        client.force_login(staff_user)

        html = client.get(SCHEDULE_URL).content.decode()

        monday = _day_block(html, 0)
        assert monday.count('class="formset-row') == 1
        assert 'data-removed' not in re.sub(r'data-removed-(note|label)', '', monday)
        assert ProfessionalSchedule.objects.filter(pk=tramo.pk).exists()

    def test_two_tramos_the_same_day_are_saved_as_two_rows(self, client, staff_user, professional):
        client.force_login(staff_user)
        response = client.post(SCHEDULE_URL, _management(**{
            'schedules-TOTAL_FORMS': '2',
            'schedules-0-day_of_week': '0', 'schedules-0-start_time': '09:00',
            'schedules-0-end_time': '14:00', 'schedules-0-is_active': 'on',
            'schedules-1-day_of_week': '0', 'schedules-1-start_time': '16:00',
            'schedules-1-end_time': '20:00', 'schedules-1-is_active': 'on',
        }))

        assert response.status_code == 302
        saved = ProfessionalSchedule.objects.filter(professional=professional, day_of_week=0)
        assert [(t.start_time, t.end_time) for t in saved] == [
            (time(9, 0), time(14, 0)), (time(16, 0), time(20, 0)),
        ]

    def test_the_admins_professional_edit_page_uses_the_same_layout(
        self, client, admin_user, professional_a
    ):
        _tramo(professional_a, MONDAY, time(9, 0), time(14, 0))
        client.force_login(admin_user)

        html = client.get(reverse('professionals:edit', args=[professional_a.pk])).content.decode()

        assert [int(d) for d in re.findall(r'data-day="(\d)"', html)] == [0, 1, 2, 3, 4, 5, 6]
        assert 'value="09:00"' in _day_block(html, 0)
