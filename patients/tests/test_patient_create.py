from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.models import Clinic, User
from patients.models import Patient


class PatientCreateViewTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(clinic_id='c1', name='Clínica Uno')
        self.user = User.objects.create_user(
            email='staff@c1.com', password='x', clinic=self.clinic, role=User.Role.STAFF
        )
        self.client.force_login(self.user)
        self.url = reverse('patients:create')

    def _data(self, **overrides):
        data = {
            'first_name': 'Ana',
            'last_name': 'García',
            'email': 'ana@example.com',
            'phone': '600111222',
            'date_of_birth': '',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_valid_post_creates_patient_in_user_clinic(self):
        resp = self.client.post(self.url, self._data())
        self.assertEqual(resp.status_code, 302)
        patient = Patient.objects.get()
        self.assertEqual(patient.clinic, self.clinic)
        self.assertEqual(patient.first_name, 'Ana')

    def test_phone_is_normalized_through_service(self):
        self.client.post(self.url, self._data(phone='600111222'))
        patient = Patient.objects.get()
        self.assertEqual(patient.phone, '+34600111222')

    def test_view_delegates_to_create_patient_service(self):
        with mock.patch('patients.views.create_patient') as mocked:
            mocked.return_value = mock.Mock(pk=1)
            self.client.post(self.url, self._data())
            self.assertEqual(mocked.call_count, 1)
            _, kwargs = mocked.call_args
            self.assertEqual(kwargs['clinic'], self.clinic)
            self.assertEqual(kwargs['first_name'], 'Ana')

    def test_invalid_phone_is_rejected(self):
        resp = self.client.post(self.url, self._data(phone='123'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Patient.objects.exists())
        self.assertIn('phone', resp.context['form'].errors)

    def test_next_redirect_appends_patient_id(self):
        next_url = reverse('appointments:create') + '?date=2030-01-15'
        resp = self.client.post(self.url, self._data(next=next_url))
        self.assertEqual(resp.status_code, 302)
        patient = Patient.objects.get()
        self.assertIn(f'patient={patient.pk}', resp.url)
        self.assertIn('date=2030-01-15', resp.url)
