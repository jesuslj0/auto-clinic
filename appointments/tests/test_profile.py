import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from appointments.models import Professional
from core.models import Clinic, User
from services.models import Service


def _tiny_png():
    # PNG 1x1 mínimo válido.
    data = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
        b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    return SimpleUploadedFile('avatar.png', data, content_type='image/png')


@override_settings(
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class ProfessionalProfileViewTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(clinic_id='c1', name='Clínica Uno')
        self.user = User.objects.create_user(
            email='pro@c1.com', password='x', clinic=self.clinic, role=User.Role.STAFF
        )
        # El signal crea el Professional automáticamente.
        self.professional = self.user.professional_profile
        self.service = Service.objects.create(clinic=self.clinic, name='Limpieza', price='30.00')
        self.client.force_login(self.user)
        self.url = reverse('appointments:profile')

    def test_get_renders_own_profile(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['professional'], self.professional)

    def test_post_updates_name_type_and_services(self):
        resp = self.client.post(self.url, {
            'first_name': 'Lucía',
            'last_name': 'Martín',
            'professional_type': Professional.ProfessionalType.DENTISTA,
            'services': [self.service.pk],
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.professional.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Lucía')
        self.assertEqual(self.user.last_name, 'Martín')
        self.assertEqual(self.professional.professional_type, Professional.ProfessionalType.DENTISTA)
        self.assertIn(self.service, self.professional.services.all())

    def test_post_uploads_photo(self):
        resp = self.client.post(self.url, {
            'first_name': 'Lucía',
            'last_name': 'Martín',
            'professional_type': Professional.ProfessionalType.MEDICO,
            'photo': _tiny_png(),
        })
        self.assertEqual(resp.status_code, 302)
        self.professional.refresh_from_db()
        self.assertTrue(self.professional.photo.name)

    def test_services_limited_to_own_clinic(self):
        other = Clinic.objects.create(clinic_id='c2', name='Otra')
        foreign = Service.objects.create(clinic=other, name='Ajeno', price='10.00')
        resp = self.client.get(self.url)
        qs = resp.context['form'].fields['services'].queryset
        self.assertIn(self.service, qs)
        self.assertNotIn(foreign, qs)
