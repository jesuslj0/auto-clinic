"""La clave del agente la gestiona la plataforma desde el admin, no la clínica.

El panel de la clínica ya no la muestra ni la rota: el equipo que mantiene el
workflow de n8n es quien la ve y quien la regenera.
"""
import pytest
from django.contrib.admin.models import LogEntry
from django.urls import reverse


@pytest.mark.django_db
class TestPanelDoesNotExposeTheKey:
    def test_key_is_not_rendered_in_the_panel(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        response = client.get(reverse('core:clinic-integrations'))
        assert str(clinic_a.agent_api_key) not in response.content.decode()

    def test_legacy_rotate_action_no_longer_rotates(self, client, admin_user, clinic_a):
        """El POST antiguo cae en el formulario normal y deja la clave intacta."""
        client.force_login(admin_user)
        before = clinic_a.agent_api_key

        client.post(reverse('core:clinic-integrations'), data={'action': 'rotate_api_key'})

        clinic_a.refresh_from_db()
        assert clinic_a.agent_api_key == before


@pytest.mark.django_db
class TestAdminRegeneratesTheKey:
    ACTION = 'regenerate_agent_api_key'

    def _run_action(self, admin_site_client, clinics):
        return admin_site_client.post(
            reverse('admin:core_clinic_changelist'),
            data={
                'action': self.ACTION,
                '_selected_action': [c.pk for c in clinics],
            },
            follow=True,
        )

    def test_action_replaces_the_key(self, admin_site_client, clinic_a):
        before = clinic_a.agent_api_key
        response = self._run_action(admin_site_client, [clinic_a])

        assert response.status_code == 200
        clinic_a.refresh_from_db()
        assert clinic_a.agent_api_key != before

    def test_action_leaves_a_trace_in_the_admin_log(self, admin_site_client, clinic_a):
        self._run_action(admin_site_client, [clinic_a])
        assert LogEntry.objects.filter(
            object_id=str(clinic_a.pk), change_message='Clave del agente regenerada'
        ).exists()

    def test_action_only_touches_the_selected_clinics(
        self, admin_site_client, clinic_a, clinic_b
    ):
        untouched = clinic_b.agent_api_key
        self._run_action(admin_site_client, [clinic_a])

        clinic_b.refresh_from_db()
        assert clinic_b.agent_api_key == untouched

    def test_key_is_visible_but_read_only_in_the_change_form(
        self, admin_site_client, clinic_a
    ):
        response = admin_site_client.get(
            reverse('admin:core_clinic_change', args=[clinic_a.pk])
        )
        html = response.content.decode()
        assert str(clinic_a.agent_api_key) in html
        assert 'name="agent_api_key"' not in html
