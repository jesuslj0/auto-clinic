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
        # Hora fija de HOY en local, no `now() + 1h`: el panel solo pinta la
        # agenda del día, así que con la hora relativa el test se caía cada vez
        # que se ejecutaba a última hora y la cita se iba al día siguiente.
        start = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        appt = Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            scheduled_at=start,
            end_at=start + timedelta(hours=1),
            status=Appointment.Status.PENDING,
        )

        response = client.get(reverse('core:dashboard'))
        content = response.content.decode()

        assert response.status_code == 200
        assert str(appt.pk) in content
        assert 'Gestionar' in content


@pytest.mark.django_db
class TestTarjetaCanceladas:
    """La cifra de la tarjeta y la lista a la que lleva tienen que coincidir.

    Son dos sitios que responden a la misma pregunta, y basta una condición de
    más en uno para que el panel se contradiga consigo mismo: pinchar un «3» y
    encontrarse cuatro filas.
    """

    @pytest.fixture
    def canceladas(self, db, clinic_a, patient_a, service_a, professional_a):
        """Una cancelada de este mes y otra del que viene."""
        from appointments.filters import month_bounds

        hoy = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        _, fin_de_mes = month_bounds(hoy.date())
        siguiente = hoy.replace(
            year=fin_de_mes.year, month=fin_de_mes.month, day=fin_de_mes.day
        ) + timedelta(days=3)

        def _crear(cuando):
            return Appointment.objects.create(
                clinic=clinic_a, patient=patient_a, service=service_a,
                professional=professional_a, scheduled_at=cuando,
                status=Appointment.Status.CANCELLED,
            )

        return {'este_mes': _crear(hoy), 'mes_que_viene': _crear(siguiente)}

    def test_la_cifra_solo_cuenta_el_mes_en_curso(self, client, admin_user, canceladas):
        """Con solo el `gte`, la del mes que viene se colaba en el recuento."""
        client.force_login(admin_user)
        response = client.get(reverse('core:dashboard'))
        assert response.context['cancelled_appointments'] == 1

    def test_el_enlace_lleva_a_esas_mismas_citas(self, client, admin_user, canceladas):
        client.force_login(admin_user)
        panel = client.get(reverse('core:dashboard'))
        listado = client.get(reverse('appointments:list'), {
            'desde': panel.context['month_start'].isoformat(),
            'hasta': panel.context['month_end'].isoformat(),
            'profesional': '',
            'status': 'cancelled',
        })
        ids = {a.pk for a in listado.context['appointments']}
        assert ids == {canceladas['este_mes'].pk}
        assert listado.context['paginator'].count == panel.context['cancelled_appointments']
