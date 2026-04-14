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

    def test_dashboard_manage_button_visible_for_pending(self, client, admin_user, clinic_a, patient_a, service_a):
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
        assert str(appt.pk) in content
        assert 'Gestionar' in content
