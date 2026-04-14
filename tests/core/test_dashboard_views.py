from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment


@pytest.mark.django_db
class TestDashboardAppointmentActions:
    def test_dashboard_confirm_action_updates_status(self, client, admin_user, appointment_a):
        client.force_login(admin_user)
        response = client.post(
            reverse('core:dashboard-appointment-action', args=[appointment_a.pk]),
            {'action': 'confirm'},
        )

        assert response.status_code == 302
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CONFIRMED

    def test_dashboard_reject_action_updates_status(self, client, admin_user, appointment_a):
        client.force_login(admin_user)
        response = client.post(
            reverse('core:dashboard-appointment-action', args=[appointment_a.pk]),
            {'action': 'reject'},
        )

        assert response.status_code == 302
        appointment_a.refresh_from_db()
        assert appointment_a.status == Appointment.Status.CANCELLED

    def test_dashboard_actions_are_scoped_by_clinic(self, client, admin_user, appointment_b):
        client.force_login(admin_user)
        response = client.post(
            reverse('core:dashboard-appointment-action', args=[appointment_b.pk]),
            {'action': 'confirm'},
        )

        assert response.status_code == 302
        appointment_b.refresh_from_db()
        assert appointment_b.status == Appointment.Status.PENDING


@pytest.mark.django_db
class TestDashboardView:
    def test_dashboard_shows_only_clinic_appointments(self, client, admin_user, appointment_a, appointment_b):
        client.force_login(admin_user)
        response = client.get(reverse('core:dashboard'))

        assert response.status_code == 200
        schedule = response.context['today_schedule']
        assert all(appointment.clinic_id == admin_user.clinic_id for appointment in schedule)
        assert appointment_b not in schedule

    def test_dashboard_manage_button_links_to_management_page(self, client, admin_user, clinic_a, patient_a, service_a):
        client.force_login(admin_user)
        appt = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=timezone.now() + timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=2),
            status=Appointment.Status.PENDING,
        )

        response = client.get(reverse('core:dashboard'))
        content = response.content.decode()

        assert response.status_code == 200
        assert reverse('core:dashboard-manage-appointment', args=[appt.pk]) in content


@pytest.mark.django_db
class TestDashboardManageAppointmentView:
    def test_manage_view_shows_appointment_detail(self, client, admin_user, appointment_a):
        client.force_login(admin_user)
        response = client.get(reverse('core:dashboard-manage-appointment', args=[appointment_a.pk]))

        assert response.status_code == 200
        assert response.context['appointment'].pk == appointment_a.pk

    def test_manage_view_is_scoped_by_clinic(self, client, admin_user, appointment_b):
        client.force_login(admin_user)
        response = client.get(reverse('core:dashboard-manage-appointment', args=[appointment_b.pk]))

        assert response.status_code == 403
