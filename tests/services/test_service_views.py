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
