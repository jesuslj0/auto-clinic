"""Ficha del paciente por pestañas: URL real, fragmento para HTMX y auditoría.

Lo que se comprueba aquí es el esqueleto, no el contenido clínico (que llega en
fases posteriores): que cada pestaña sea una URL navegable, que con `HX-Request`
devuelva solo el fragmento, que toda lectura deje su `AccessLog` y que el
aislamiento por clínica siga siendo un 404 sin registro de acceso.
"""
import pytest
from django.urls import reverse

from audit.models import AccessLog
from clinical.models import ClinicalAlert


TABS = [
    'patients:detail',
    'patients:tab-anamnesis',
    'patients:tab-alerts',
    'patients:tab-lesions',
    'patients:tab-consents',
    'patients:tab-procedures',
]


@pytest.mark.django_db
@pytest.mark.parametrize('name', TABS)
def test_full_page(client, admin_user, patient_a, name):
    """Una URL pegada en el navegador o un F5 devuelven la página entera."""
    client.force_login(admin_user)
    r = client.get(reverse(name, kwargs={'id': patient_a.pk}))
    assert r.status_code == 200
    html = r.content.decode()
    assert '<!DOCTYPE html>' in html
    assert 'patient-tab-region' in html
    assert 'role="tablist"' in html
    assert AccessLog.objects.filter(action=AccessLog.Action.VIEW, patient=patient_a).exists()


@pytest.mark.django_db
@pytest.mark.parametrize('name', TABS)
def test_htmx_partial(client, admin_user, patient_a, name):
    """Con `HX-Request` solo viaja el fragmento: barra de pestañas + panel."""
    client.force_login(admin_user)
    r = client.get(reverse(name, kwargs={'id': patient_a.pk}), HTTP_HX_REQUEST='true')
    assert r.status_code == 200
    html = r.content.decode()
    assert '<!DOCTYPE html>' not in html
    assert 'role="tablist"' in html
    assert 'patient-tab-panel' in html
    assert AccessLog.objects.filter(action=AccessLog.Action.VIEW, patient=patient_a).exists()


@pytest.mark.django_db
def test_history_restore_returns_full_page(client, admin_user, patient_a):
    """Al restaurar el historial, htmx repone el `body`: hay que darle la página."""
    client.force_login(admin_user)
    r = client.get(
        reverse('patients:tab-alerts', kwargs={'id': patient_a.pk}),
        HTTP_HX_REQUEST='true',
        HTTP_HX_HISTORY_RESTORE_REQUEST='true',
    )
    assert '<!DOCTYPE html>' in r.content.decode()


@pytest.mark.django_db
def test_alert_banner_and_ordering(client, admin_user, patient_a):
    """La banda pinta las vigentes, lo crítico primero, y omite las desactivadas."""
    ClinicalAlert.objects.create(
        patient=patient_a, alert_type=ClinicalAlert.AlertType.OTHER,
        severity=ClinicalAlert.Severity.INFO, note='Nota informativa',
    )
    ClinicalAlert.objects.create(
        patient=patient_a, alert_type=ClinicalAlert.AlertType.DIABETES,
        severity=ClinicalAlert.Severity.CRITICAL,
    )
    inactive = ClinicalAlert.objects.create(
        patient=patient_a, alert_type=ClinicalAlert.AlertType.NEUROPATHY,
        severity=ClinicalAlert.Severity.WARNING,
    )
    inactive.deactivate()

    client.force_login(admin_user)
    r = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))
    html = r.content.decode()
    assert 'Alertas clínicas activas' in html
    assert 'Neuropatía' not in html
    assert html.index('Diabetes') < html.index('Otra')
    assert r.context['has_critical_alerts'] is True

    r2 = client.get(reverse('patients:tab-alerts', kwargs={'id': patient_a.pk}))
    assert 'Nota informativa' in r2.content.decode()


@pytest.mark.django_db
def test_no_banner_without_alerts(client, admin_user, patient_a):
    """Sin alertas activas, la banda no ocupa espacio."""
    client.force_login(admin_user)
    r = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))
    assert 'Alertas clínicas activas' not in r.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize('name', TABS)
def test_isolation_and_login(client, admin_user, patient_b, name):
    """Sin sesión, redirección; con otra clínica, 404 y ningún acceso registrado."""
    url = reverse(name, kwargs={'id': patient_b.pk})
    assert client.get(url).status_code == 302
    client.force_login(admin_user)
    assert client.get(url).status_code == 404
    assert not AccessLog.objects.exists()
