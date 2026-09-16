import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestServiceTemplateViews:
    def test_edit_view_updates_service(self, client, admin_user, service_a):
        client.force_login(admin_user)
        response = client.post(
            reverse('services:edit', args=[service_a.pk]),
            {
                'name': 'Consulta extendida',
                'description': 'Nueva descripción',
                'duration_minutes': 45,
                'price': '75.00',
                'is_active': 'on',
            },
        )

        assert response.status_code == 302
        service_a.refresh_from_db()
        assert service_a.name == 'Consulta extendida'
        assert service_a.duration_minutes == 45

    def test_service_list_includes_edit_link(self, client, admin_user, service_a):
        client.force_login(admin_user)
        response = client.get(reverse('services:list'))

        assert response.status_code == 200
        assert reverse('services:edit', args=[service_a.pk]) in response.content.decode()

    def test_user_cannot_edit_service_from_other_clinic(self, client, admin_user, service_b):
        client.force_login(admin_user)
        response = client.get(reverse('services:edit', args=[service_b.pk]))

        assert response.status_code == 404


@pytest.mark.django_db
class TestServiceDeleteView:
    def test_edit_view_shows_delete_modal(self, client, admin_user, service_a):
        client.force_login(admin_user)
        response = client.get(reverse('services:edit', args=[service_a.pk]))

        assert response.status_code == 200
        assert reverse('services:delete', args=[service_a.pk]) in response.content.decode()

    def test_create_view_has_no_delete_button(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse('services:create'))

        assert 'modal-delete-title' not in response.content.decode()

    def test_deletes_service_without_appointments(self, client, admin_user, service_a):
        from services.models import Service

        client.force_login(admin_user)
        response = client.post(reverse('services:delete', args=[service_a.pk]))

        assert response.status_code == 302
        assert response.url == reverse('services:list')
        assert not Service.objects.filter(pk=service_a.pk).exists()

    def test_service_with_appointments_is_not_deleted(self, client, admin_user, service_a, appointment_a):
        from services.models import Service

        client.force_login(admin_user)
        response = client.post(reverse('services:delete', args=[service_a.pk]))

        assert response.status_code == 302
        assert response.url == reverse('services:edit', args=[service_a.pk])
        assert Service.objects.filter(pk=service_a.pk).exists()
        appointment_a.refresh_from_db()
        assert appointment_a.service_id == service_a.pk

    def test_modal_explains_block_when_service_has_appointments(self, client, admin_user, service_a, appointment_a):
        client.force_login(admin_user)
        response = client.get(reverse('services:edit', args=[service_a.pk]))
        content = response.content.decode()

        assert 'No se puede eliminar' in content
        assert reverse('services:delete', args=[service_a.pk]) not in content

    def test_get_is_not_allowed(self, client, admin_user, service_a):
        client.force_login(admin_user)
        response = client.get(reverse('services:delete', args=[service_a.pk]))

        assert response.status_code == 405

    def test_cannot_delete_service_from_other_clinic(self, client, admin_user, service_b):
        from services.models import Service

        client.force_login(admin_user)
        response = client.post(reverse('services:delete', args=[service_b.pk]))

        assert response.status_code == 404
        assert Service.objects.filter(pk=service_b.pk).exists()

    def test_requires_login(self, client, service_a):
        from services.models import Service

        response = client.post(reverse('services:delete', args=[service_a.pk]))

        assert response.status_code == 302
        assert Service.objects.filter(pk=service_a.pk).exists()


@pytest.mark.django_db
class TestServiceAdminOnly:
    def test_staff_cannot_open_edit(self, client, staff_user, service_a):
        client.force_login(staff_user)
        response = client.get(reverse('services:edit', args=[service_a.pk]))

        assert response.status_code == 403

    def test_staff_cannot_update(self, client, staff_user, service_a):
        client.force_login(staff_user)
        response = client.post(
            reverse('services:edit', args=[service_a.pk]),
            {'name': 'Cambiado', 'duration_minutes': 30, 'price': '1.00', 'is_active': 'on'},
        )

        assert response.status_code == 403
        service_a.refresh_from_db()
        assert service_a.name == 'Consultation'

    def test_staff_cannot_delete(self, client, staff_user, service_a):
        from services.models import Service

        client.force_login(staff_user)
        response = client.post(reverse('services:delete', args=[service_a.pk]))

        assert response.status_code == 403
        assert Service.objects.filter(pk=service_a.pk).exists()

    def test_list_hides_edit_link_for_staff(self, client, staff_user, service_a):
        client.force_login(staff_user)
        response = client.get(reverse('services:list'))

        assert response.status_code == 200
        assert reverse('services:edit', args=[service_a.pk]) not in response.content.decode()


@pytest.mark.django_db
class TestServiceListOrdering:
    def test_active_services_come_first_with_separator(self, client, admin_user, clinic_a):
        from services.models import Service

        Service.objects.create(clinic=clinic_a, name='Aaa inactivo', price='10.00', is_active=False)
        Service.objects.create(clinic=clinic_a, name='Zzz activo', price='10.00', is_active=True)

        client.force_login(admin_user)
        response = client.get(reverse('services:list'))

        nombres = [s.name for s in response.context['services']]
        assert nombres == ['Zzz activo', 'Aaa inactivo']
        content = response.content.decode()
        assert content.index('Zzz activo') < content.index('role="separator"') < content.index('Aaa inactivo')

    def test_no_separator_when_all_services_are_active(self, client, admin_user, service_a):
        client.force_login(admin_user)
        response = client.get(reverse('services:list'))

        assert 'role="separator"' not in response.content.decode()


@pytest.mark.django_db
class TestServiceListUsageCounts:
    def _service_in_list(self, client, service):
        response = client.get(reverse('services:list'))
        return next(s for s in response.context['services'] if s.pk == service.pk), response

    def test_counts_appointments_and_procedures_without_multiplying(
        self, client, admin_user, service_a, appointment_a, procedure_a, visit_a
    ):
        from datetime import timedelta

        from appointments.models import Appointment
        from clinical.models import PerformedProcedure

        # Segunda cita: con dos citas y dos procedimientos, un JOIN doble daría 4 y 4.
        Appointment.objects.create(
            clinic=appointment_a.clinic,
            patient=appointment_a.patient,
            service=service_a,
            professional=appointment_a.professional,
            scheduled_at=appointment_a.scheduled_at + timedelta(days=1),
            end_at=appointment_a.end_at + timedelta(days=1),
        )
        PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        client.force_login(admin_user)
        service, response = self._service_in_list(client, service_a)

        assert service.appointments_count == 2
        assert service.procedures_count == 2
        content = response.content.decode()
        assert '2 citas' in content
        assert '2 procedimientos' in content

    def test_soft_deleted_procedures_do_not_count(self, client, admin_user, service_a, procedure_a):
        procedure_a.delete()

        client.force_login(admin_user)
        service, _ = self._service_in_list(client, service_a)

        assert service.procedures_count == 0

    def test_service_without_usage_shows_zero(self, client, admin_user, service_a):
        client.force_login(admin_user)
        service, response = self._service_in_list(client, service_a)

        assert service.appointments_count == 0
        assert service.procedures_count == 0
        assert '0 citas' in response.content.decode()

    def test_query_count_does_not_grow_with_services(self, client, admin_user, clinic_a, service_a):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from services.models import Service

        client.force_login(admin_user)
        with CaptureQueriesContext(connection) as uno:
            client.get(reverse('services:list'))

        for i in range(5):
            Service.objects.create(clinic=clinic_a, name=f'Extra {i}', price='10.00')
        with CaptureQueriesContext(connection) as seis:
            client.get(reverse('services:list'))

        assert len(seis) == len(uno)


@pytest.mark.django_db
def test_list_shows_totals_in_filter_buttons(client, admin_user, clinic_a):
    from services.models import Service

    Service.objects.create(clinic=clinic_a, name='Uno', price='10.00', is_active=True)
    Service.objects.create(clinic=clinic_a, name='Dos', price='10.00', is_active=True)
    Service.objects.create(clinic=clinic_a, name='Tres', price='10.00', is_active=False)

    client.force_login(admin_user)
    response = client.get(reverse('services:list'))

    assert response.context['total_count'] == 3
    assert response.context['active_count'] == 2
    assert response.context['inactive_count'] == 1


@pytest.mark.django_db
class TestServiceEditStats:
    def test_edit_shows_usage_stats(self, client, admin_user, service_a, appointment_a, procedure_a):
        from decimal import Decimal

        client.force_login(admin_user)
        response = client.get(reverse('services:edit', args=[service_a.pk]))

        stats = response.context['stats']
        assert stats['appointments']['total'] == 1
        assert stats['appointments']['upcoming'] == 1
        by_status = {row['status']: row['count'] for row in stats['appointments']['by_status']}
        assert by_status['pending'] == 1
        assert by_status['completed'] == 0
        assert stats['procedures']['total'] == 1
        assert stats['procedures']['pending_invoice'] == 1
        assert stats['procedures']['amount'] == Decimal('50.00')
        assert stats['procedures']['last_performed'] == procedure_a.performed_at
        assert 'Resumen de uso del servicio' in response.content.decode()

    def test_edit_stats_without_usage(self, client, admin_user, service_a):
        from decimal import Decimal

        client.force_login(admin_user)
        response = client.get(reverse('services:edit', args=[service_a.pk]))

        stats = response.context['stats']
        assert stats['appointments']['total'] == 0
        assert stats['procedures']['amount'] == Decimal('0')
        assert stats['procedures']['last_performed'] is None

    def test_create_has_no_stats_column(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse('services:create'))

        assert 'Resumen de uso del servicio' not in response.content.decode()


@pytest.mark.django_db
class TestServiceSaveButtonState:
    def test_edit_save_button_starts_clean(self, client, admin_user, service_a):
        client.force_login(admin_user)
        content = client.get(reverse('services:edit', args=[service_a.pk])).content.decode()

        assert ':disabled="!dirty"' in content
        assert 'dirty: false,' in content

    def test_invalid_post_keeps_save_button_enabled(self, client, admin_user, service_a):
        client.force_login(admin_user)
        content = client.post(reverse('services:edit', args=[service_a.pk]), {'name': ''}).content.decode()

        assert 'dirty: true,' in content

    def test_create_save_button_is_always_enabled(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse('services:create')).content.decode()

        assert ':disabled="!dirty"' not in content


@pytest.mark.django_db
def test_edit_stats_status_rows_always_add_up_to_total(client, admin_user, clinic_a, patient_a, service_a):
    """Una cita confirmada con fecha pasada no es «próxima», pero sí tiene su fila."""
    from datetime import timedelta

    from django.utils import timezone

    from appointments.models import Appointment

    professional = admin_user.professional_profile
    professional.services.add(service_a)
    for dias, status in ((10, 'confirmed'), (9, 'confirmed'), (8, 'cancelled')):
        inicio = timezone.now() - timedelta(days=dias)
        Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=professional,
            scheduled_at=inicio, end_at=inicio + timedelta(minutes=30), status=status,
        )

    client.force_login(admin_user)
    stats = client.get(reverse('services:edit', args=[service_a.pk])).context['stats']['appointments']

    by_status = {row['status']: row['count'] for row in stats['by_status']}
    assert stats['total'] == 3
    assert by_status['confirmed'] == 2
    assert by_status['cancelled'] == 1
    assert sum(by_status.values()) == stats['total']
    assert stats['upcoming'] == 0
