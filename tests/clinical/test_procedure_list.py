"""Listado global de procedimientos y aviso de citas completadas sin procedimiento.

Lo que se defiende:

1. **Aislamiento**: solo salen los procedimientos de la clínica del usuario.
2. **Auditoría**: leerlo deja `AccessLog` (es dato clínico, a diferencia del
   listado de citas), y el agente no entra.
3. **Lo congelado**: se enseña y se suma `frozen_price`, no el catálogo.
4. **Filtros**: periodo (mes por defecto), paciente, profesional, servicio y
   facturación; los dados de baja no cuentan.
5. **Aviso**: una cita completada, con ficha y sin procedimiento cuenta; con
   procedimiento, sin ficha, vieja o de otra clínica, no.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.filters import UNRECORDED_WINDOW_DAYS, completed_without_procedure
from appointments.models import Appointment
from audit.models import AccessLog
from clinical.models import PerformedProcedure, Visit
from services.models import Service

URL = reverse('clinical:procedure-list')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


def do_procedure(visit, service, *, price=None, when=None):
    procedure = PerformedProcedure.objects.create(
        visit=visit, service=service, **({'frozen_price': price} if price is not None else {})
    )
    if when is not None:
        PerformedProcedure.objects.filter(pk=procedure.pk).update(performed_at=when)
        procedure.refresh_from_db()
    return procedure


@pytest.mark.django_db
class TestListing:
    def test_shows_the_clinics_procedures_with_frozen_price(
        self, panel_client, procedure_a, service_a
    ):
        # El catálogo sube después: lo que se enseña es lo que costó ese día.
        Service.objects.filter(pk=service_a.pk).update(price=Decimal('999.00'))
        response = panel_client.get(URL)
        assert response.status_code == 200
        html = response.content.decode()
        assert procedure_a.frozen_service_name in html
        assert '999' not in html
        assert response.context['totals']['count'] == 1
        assert response.context['totals']['total'] == procedure_a.frozen_price

    def test_other_clinic_is_not_visible(
        self, panel_client, procedure_a, clinic_b, patient_b, service_b, professional_a
    ):
        from clinical.models import Episode

        history = patient_b.medical_history
        episode = Episode.objects.create(history=history, reason='x')
        visit = Visit.objects.create(episode=episode, professional=professional_a)
        other = do_procedure(visit, service_b)
        response = panel_client.get(URL)
        assert [p.pk for p in response.context['procedures']] == [procedure_a.pk]
        assert other.frozen_service_name not in response.content.decode() or (
            other.frozen_service_name == procedure_a.frozen_service_name
        )

    def test_soft_deleted_procedures_do_not_count(self, panel_client, procedure_a):
        procedure_a.delete()
        assert panel_client.get(URL).context['totals']['count'] == 0

    def test_anonymous_is_redirected(self, client):
        assert client.get(URL).status_code in (302, 403)

    def test_default_period_is_the_current_month(self, panel_client, visit_a, service_a):
        old = timezone.now() - timedelta(days=90)
        do_procedure(visit_a, service_a, when=old)
        assert panel_client.get(URL).context['totals']['count'] == 0
        response = panel_client.get(URL, {'desde': '', 'hasta': ''})
        assert response.context['totals']['count'] == 1


@pytest.mark.django_db
class TestFilters:
    def test_billing_filter(self, panel_client, procedure_a, issued_invoice_a, visit_a, service_a):
        # `procedure_a` ya está facturado por `issued_invoice_a`; este otro no.
        pending = do_procedure(visit_a, service_a)
        ids = lambda **q: {p.pk for p in panel_client.get(URL, q).context['procedures']}
        assert ids(cobro='pendiente') == {pending.pk}
        assert ids(cobro='facturado') == {procedure_a.pk}

    def test_totals_split_pending(self, panel_client, procedure_a, issued_invoice_a, visit_a, service_a):
        pending = do_procedure(visit_a, service_a, price=Decimal('12.50'))
        totals = panel_client.get(URL).context['totals']
        assert totals['count'] == 2
        assert totals['pending_count'] == 1
        assert totals['pending_total'] == Decimal('12.50')

    def test_patient_search(self, panel_client, procedure_a):
        assert panel_client.get(URL, {'q': 'doe'}).context['totals']['count'] == 1
        assert panel_client.get(URL, {'q': 'nadie'}).context['totals']['count'] == 0

    def test_patient_id_filter(self, panel_client, procedure_a, patient_a):
        response = panel_client.get(URL, {'paciente': patient_a.pk, 'desde': '', 'hasta': ''})
        assert response.context['totals']['count'] == 1
        assert response.context['filter_patient'] == patient_a
        assert panel_client.get(URL, {'paciente': patient_a.pk + 999}).context['totals']['count'] == 0

    def test_service_and_professional_filters(self, panel_client, procedure_a, service_a, professional_a):
        procedure_a.created_by = professional_a
        procedure_a.save(update_fields=['created_by', 'updated_at'])
        assert panel_client.get(URL, {'servicio': service_a.pk}).context['totals']['count'] == 1
        assert panel_client.get(URL, {'servicio': service_a.pk + 999}).context['totals']['count'] == 0
        assert panel_client.get(URL, {'profesional': professional_a.pk}).context['totals']['count'] == 1

    def test_garbage_params_fall_back_to_defaults(self, panel_client, procedure_a):
        response = panel_client.get(URL, {'desde': 'no', 'cobro': 'x', 'sort': 'zzz', 'servicio': 'abc'})
        assert response.status_code == 200
        assert response.context['filters'].billing == ''
        assert response.context['filters'].sort == 'desc'


@pytest.mark.django_db
class TestAudit:
    def test_listing_is_logged(self, panel_client, admin_user, procedure_a):
        panel_client.get(URL)
        log = AccessLog.objects.filter(user=admin_user).latest('id')
        assert log.action == AccessLog.Action.LIST
        assert log.path.endswith('/clinico/procedimientos/')

    def test_searching_by_patient_is_logged_as_search(self, panel_client, admin_user, procedure_a):
        panel_client.get(URL, {'q': 'doe'})
        assert AccessLog.objects.filter(user=admin_user).latest('id').action == AccessLog.Action.SEARCH

    def test_agent_api_key_is_denied(self, client, clinic_a, procedure_a):
        """El `Api-Key` de n8n no autentica una vista de sesión: va al login."""
        response = client.get(URL, HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}')
        assert response.status_code == 302
        assert '/login/' in response['Location']


@pytest.mark.django_db
class TestUnrecordedWarning:
    def make(self, clinic, patient, service, professional, *, status, days_ago=1, patient_set=True):
        when = (timezone.localtime() - timedelta(days=days_ago)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return Appointment.objects.create(
            clinic=clinic, patient=patient if patient_set else None, patient_name='Sin Ficha',
            service=service, professional=professional, scheduled_at=when,
            end_at=when + timedelta(minutes=30), status=status,
        )

    def count(self, clinic):
        since = timezone.now() - timedelta(days=UNRECORDED_WINDOW_DAYS)
        return completed_without_procedure(
            Appointment.objects.filter(clinic=clinic), since=since
        ).count()

    def test_completed_without_procedure_counts(
        self, clinic_a, patient_a, service_a, professional_a
    ):
        S = Appointment.Status
        self.make(clinic_a, patient_a, service_a, professional_a, status=S.COMPLETED, days_ago=1)
        self.make(clinic_a, patient_a, service_a, professional_a, status=S.CONFIRMED, days_ago=2)
        self.make(clinic_a, patient_a, service_a, professional_a, status=S.COMPLETED, days_ago=3, patient_set=False)
        self.make(clinic_a, patient_a, service_a, professional_a, status=S.COMPLETED, days_ago=UNRECORDED_WINDOW_DAYS + 5)
        assert self.count(clinic_a) == 1

    def test_completed_with_procedure_does_not_count(
        self, clinic_a, patient_a, service_a, professional_a, visit_a
    ):
        appointment = self.make(
            clinic_a, patient_a, service_a, professional_a, status=Appointment.Status.COMPLETED
        )
        do_procedure(visit_a, service_a)
        Visit.objects.filter(pk=visit_a.pk).update(appointment=appointment)
        assert self.count(clinic_a) == 0

    def test_banner_in_the_list_links_to_the_filtered_appointments(
        self, panel_client, clinic_a, patient_a, service_a, professional_a
    ):
        self.make(clinic_a, patient_a, service_a, professional_a, status=Appointment.Status.COMPLETED)
        response = panel_client.get(URL)
        assert response.context['unrecorded_count'] == 1
        html = response.content.decode()
        assert 'completada sin procedimiento registrado' in html
        assert 'procedimiento=sin' in response.context['unrecorded_url']
        assert 'status=completed' in response.context['unrecorded_url']

    def test_no_banner_when_nothing_is_missing(self, panel_client):
        assert 'sin procedimiento registrado' not in panel_client.get(URL).content.decode()

    def test_dashboard_alert(self, panel_client, clinic_a, patient_a, service_a, professional_a):
        self.make(clinic_a, patient_a, service_a, professional_a, status=Appointment.Status.COMPLETED)
        alerts = panel_client.get(reverse('core:dashboard')).context['alerts']
        unrecorded = [a for a in alerts if a['key'] == 'unrecorded']
        assert len(unrecorded) == 1 and unrecorded[0]['count'] == 1
        assert 'procedimiento=sin' in unrecorded[0]['url']

    def test_other_clinic_does_not_trigger_the_alert(
        self, panel_client, clinic_b, patient_b, service_b, professional_a
    ):
        self.make(clinic_b, patient_b, service_b, professional_a, status=Appointment.Status.COMPLETED)
        alerts = panel_client.get(reverse('core:dashboard')).context['alerts']
        assert not [a for a in alerts if a['key'] == 'unrecorded']


NEW_URL = reverse('clinical:procedure-new')


@pytest.mark.django_db
class TestNewProcedureButton:
    """El botón del listado lleva a elegir paciente y de ahí al formulario de la ficha."""

    def test_list_has_the_button(self, panel_client):
        html = panel_client.get(URL).content.decode()
        assert NEW_URL in html
        assert 'Nuevo procedimiento' in html

    def test_picker_links_each_patient_to_the_procedure_form(self, panel_client, patient_a):
        response = panel_client.get(NEW_URL)
        assert response.status_code == 200
        assert reverse('patients:procedure-create', args=[patient_a.pk]) in response.content.decode()

    def test_picker_searches_by_name_and_phone(self, panel_client, patient_a):
        assert [p.pk for p in panel_client.get(NEW_URL, {'q': 'doe'}).context['patients']] == [patient_a.pk]
        assert [p.pk for p in panel_client.get(NEW_URL, {'q': '0001'}).context['patients']] == [patient_a.pk]
        assert list(panel_client.get(NEW_URL, {'q': 'nadie'}).context['patients']) == []

    def test_picker_only_offers_patients_of_the_users_clinic(self, panel_client, patient_a, patient_b):
        pks = {p.pk for p in panel_client.get(NEW_URL).context['patients']}
        assert patient_a.pk in pks and patient_b.pk not in pks

    def test_picker_is_audited(self, panel_client, admin_user, patient_a):
        panel_client.get(NEW_URL, {'q': 'doe'})
        log = AccessLog.objects.filter(user=admin_user).latest('id')
        assert log.action == AccessLog.Action.SEARCH

    def test_picker_requires_login(self, client):
        assert client.get(NEW_URL).status_code == 302
