from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, AppointmentStatusHistory
from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


@override_settings(
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
)
class AppointmentCreateViewTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(clinic_id='c1', name='Clínica Uno', timezone='Europe/Madrid')
        self.other_clinic = Clinic.objects.create(clinic_id='c2', name='Clínica Dos')

        self.user = User.objects.create_user(
            email='staff@c1.com', password='x', clinic=self.clinic, role=User.Role.STAFF
        )
        self.client.force_login(self.user)

        self.service = Service.objects.create(
            clinic=self.clinic, name='Limpieza', duration_minutes=45, price=Decimal('30.00')
        )
        self.patient = Patient.objects.create(
            clinic=self.clinic, first_name='Ana', last_name='García', phone='+34600000001'
        )

        # Recursos de otra clínica (deben ser rechazados)
        self.other_service = Service.objects.create(
            clinic=self.other_clinic, name='Otro', duration_minutes=30, price=Decimal('10.00')
        )
        self.other_patient = Patient.objects.create(
            clinic=self.other_clinic, first_name='Bob', last_name='Smith', phone='+34600000002'
        )

        self.url = reverse('appointments:create')

    def _future_date_time(self):
        """Devuelve (date_str, time_str) en hora local de la clínica, en el futuro."""
        tz = ZoneInfo(self.clinic.timezone)
        local = timezone.now().astimezone(tz) + timedelta(days=3)
        return local.strftime('%Y-%m-%d'), '10:30'

    def _post_data(self, **overrides):
        date_str, time_str = self._future_date_time()
        data = {
            'patient': self.patient.pk,
            'service': self.service.pk,
            'professional': '',
            'date': date_str,
            'time': time_str,
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_valid_post_creates_pending_appointment(self):
        resp = self.client.post(self.url, self._post_data())
        self.assertEqual(resp.status_code, 302)

        appointment = Appointment.objects.get()
        self.assertEqual(appointment.status, Appointment.Status.PENDING)
        self.assertEqual(appointment.clinic, self.clinic)
        # end_at se calcula a partir de duration_minutes del servicio
        self.assertEqual(appointment.end_at, appointment.scheduled_at + timedelta(minutes=45))

    def test_creation_goes_through_service_not_objects_create(self):
        # El paso por el service deja un registro de historial inicial None -> pending.
        self.client.post(self.url, self._post_data())
        appointment = Appointment.objects.get()
        history = AppointmentStatusHistory.objects.get(appointment=appointment)
        self.assertIsNone(history.from_status)
        self.assertEqual(history.to_status, Appointment.Status.PENDING)
        self.assertEqual(history.actor, AppointmentStatusHistory.Actor.STAFF)

    def test_view_delegates_to_create_appointment_service(self):
        with mock.patch('appointments.views.create_appointment', wraps=None) as mocked:
            mocked.return_value = mock.Mock(pk='x')
            self.client.post(self.url, self._post_data())
            self.assertEqual(mocked.call_count, 1)
            _, kwargs = mocked.call_args
            self.assertEqual(kwargs['clinic'], self.clinic)
            self.assertEqual(kwargs['service'], self.service)
            self.assertEqual(kwargs['patient'], self.patient)

    def test_scheduled_at_stored_in_utc_from_clinic_local_time(self):
        date_str, time_str = self._future_date_time()
        self.client.post(self.url, self._post_data(date=date_str, time=time_str))

        appointment = Appointment.objects.get()
        tz = ZoneInfo(self.clinic.timezone)
        expected_local = datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M').replace(tzinfo=tz)
        expected_utc = expected_local.astimezone(ZoneInfo('UTC'))

        self.assertEqual(appointment.scheduled_at, expected_utc)
        # Almacenado en UTC
        self.assertEqual(appointment.scheduled_at.utcoffset(), timedelta(0))

    def test_foreign_service_is_rejected(self):
        resp = self.client.post(self.url, self._post_data(service=self.other_service.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Appointment.objects.exists())
        self.assertIn('service', resp.context['form'].errors)

    def test_foreign_patient_is_rejected(self):
        resp = self.client.post(self.url, self._post_data(patient=self.other_patient.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Appointment.objects.exists())
        self.assertIn('patient', resp.context['form'].errors)

    def test_past_datetime_is_rejected(self):
        tz = ZoneInfo(self.clinic.timezone)
        past = timezone.now().astimezone(tz) - timedelta(days=1)
        resp = self.client.post(
            self.url, self._post_data(date=past.strftime('%Y-%m-%d'), time='10:30')
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Appointment.objects.exists())

    def test_professional_defaults_to_authenticated_professional(self):
        # El usuario staff tiene perfil de profesional (creado por signal).
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context['form'].initial['professional'],
            self.user.professional_profile.pk,
        )

    def test_query_string_professional_overrides_default(self):
        resp = self.client.get(self.url, {'professional': 999})
        self.assertEqual(resp.context['form'].initial['professional'], '999')

    def test_query_string_prefills_form(self):
        resp = self.client.get(
            self.url,
            {'patient': self.patient.pk, 'service': self.service.pk, 'date': '2030-01-15', 'time': '09:00'},
        )
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(str(form.initial['patient']), str(self.patient.pk))
        self.assertEqual(str(form.initial['service']), str(self.service.pk))
        self.assertEqual(form.initial['date'], '2030-01-15')
        self.assertEqual(form.initial['time'], '09:00')
