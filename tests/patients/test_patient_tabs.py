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
from patients.views import PATIENT_TABS


TABS = [
    'patients:detail',
    'patients:tab-documents',
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
def test_alert_badge_counts_and_the_tab_orders(client, admin_user, patient_a):
    """La cabecera lleva solo el número de vigentes; la pestaña las detalla, lo crítico primero."""
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
    assert 'data-alerts-badge' in html
    assert 'aria-label="2 alertas clínicas activas. Ver detalle"' in html     # las desactivadas no cuentan
    assert 'Neuropatía' not in html
    # Solo el número: ya no se listan los nombres en la cabecera.
    assert 'Diabetes' not in html and 'Nota informativa' not in html
    assert 'text-danger ring-danger-line' in html.split('data-alerts-badge')[1][:900]
    assert r.context['has_critical_alerts'] is True

    r2 = client.get(reverse('patients:tab-alerts', kwargs={'id': patient_a.pk}))
    tab = r2.content.decode()
    assert 'Nota informativa' in tab
    assert tab.index('Diabetes') < tab.index('Otra')


@pytest.mark.django_db
def test_badge_is_amber_without_critical_alerts(client, admin_user, patient_a):
    ClinicalAlert.objects.create(
        patient=patient_a, alert_type=ClinicalAlert.AlertType.NEUROPATHY,
        severity=ClinicalAlert.Severity.WARNING,
    )
    client.force_login(admin_user)
    html = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk})).content.decode()

    assert 'aria-label="1 alerta clínica activa. Ver detalle"' in html
    assert 'text-warning ring-warning-line' in html.split('data-alerts-badge')[1][:900]


@pytest.mark.django_db
def test_no_badge_without_alerts(client, admin_user, patient_a):
    """Sin alertas activas no se pinta nada."""
    client.force_login(admin_user)
    r = client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))
    assert 'data-alerts-badge' not in r.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize('name', TABS)
def test_isolation_and_login(client, admin_user, patient_b, name):
    """Sin sesión, redirección; con otra clínica, 404 y ningún acceso registrado."""
    url = reverse(name, kwargs={'id': patient_b.pk})
    assert client.get(url).status_code == 302
    client.force_login(admin_user)
    assert client.get(url).status_code == 404
    assert not AccessLog.objects.exists()


@pytest.mark.django_db
def test_anamnesis_and_consents_share_one_documents_tab(client, admin_user, patient_a):
    """Dos pestañas en una para que la barra quepa: «Documentos» con los dos bloques."""
    client.force_login(admin_user)
    html = client.get(reverse('patients:tab-documents', kwargs={'id': patient_a.pk})).content.decode()

    assert 'id="anamnesis"' in html and 'id="consentimientos"' in html
    assert reverse('patients:anamnesis-create', kwargs={'id': patient_a.pk}) in html
    assert reverse('patients:consent-sign', kwargs={'id': patient_a.pk}) in html
    labels = [tab['label'] for tab in PATIENT_TABS]
    assert 'Documentos' in labels
    assert 'Anamnesis' not in labels and 'Consentimientos' not in labels
    assert len(labels) <= 6


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['patients:tab-anamnesis', 'patients:tab-consents'])
def test_the_old_routes_return_the_documents_tab(client, admin_user, patient_a, name):
    """Las rutas antiguas están en marcadores y enlaces: siguen valiendo, y marcan «Documentos»."""
    client.force_login(admin_user)
    response = client.get(reverse(name, kwargs={'id': patient_a.pk}))

    assert response.status_code == 200
    assert response.context['active_tab'] == 'documents'
    assert 'responses' in response.context and 'consents' in response.context
    assert 'id="patient-tab-documents"' in response.content.decode()
