"""Alta y gestión de una factura a mano, desde el panel.

Lo que se vigila es que la interfaz no pueda hacer lo que el modelo prohíbe:
facturar a un paciente de otra clínica, colar procedimientos ajenos o ya
cobrados, editar una factura emitida, o borrarla en vez de anularla.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import PatientInvoice
from clinical.models import PerformedProcedure

CREATE_URL = reverse('billing:invoice-create')


@pytest.fixture
def panel(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def other_patient_a(db, clinic_a):
    """Otro paciente de la MISMA clínica (patient_b, en conftest, es de otra)."""
    from patients.models import Patient

    return Patient.objects.create(
        clinic=clinic_a, first_name='Otra', last_name='Paciente',
        email='otra@alpha.test', phone='555-9999',
    )


@pytest.fixture
def two_procedures(db, visit_a, service_a):
    """Dos procedimientos de patient_a sin facturar, 50 € cada uno."""
    return [
        PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        for _ in range(2)
    ]


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_alta_como_borrador(panel, patient_a, two_procedures):
    response = panel.post(CREATE_URL, {
        'patient': patient_a.pk,
        'procedures': [two_procedures[0].pk],
    })

    invoice = PatientInvoice.objects.get()
    assert response.status_code == 302
    assert response.url == reverse('billing:invoice-detail', args=[invoice.pk])
    assert invoice.is_draft
    assert invoice.number is None
    assert invoice.total == Decimal('50.00')
    assert list(invoice.procedures.all()) == [two_procedures[0]]


@pytest.mark.django_db
def test_alta_emitiendo_en_el_acto(panel, patient_a, two_procedures):
    panel.post(CREATE_URL, {
        'patient': patient_a.pk,
        'procedures': [p.pk for p in two_procedures],
        'issue_now': 'on',
    })

    invoice = PatientInvoice.objects.get()
    assert invoice.is_issued
    assert invoice.number.startswith('F-')
    assert invoice.total == Decimal('100.00')
    # Emitida: lo que dice está copiado en `lines`, no referenciado.
    assert len(invoice.lines) == 2
    assert invoice.frozen_patient_name == str(patient_a)


@pytest.mark.django_db
def test_no_se_emite_una_factura_vacia(panel, patient_a):
    response = panel.post(CREATE_URL, {'patient': patient_a.pk, 'issue_now': 'on'})

    assert response.status_code == 200          # vuelve al formulario
    assert PatientInvoice.objects.count() == 0  # y no deja nada a medias
    assert 'procedures' in response.context['form'].errors


@pytest.mark.django_db
def test_no_se_cuela_un_procedimiento_de_otro_paciente(panel, other_patient_a, procedure_a):
    """`procedure_a` es de patient_a; la factura se pide para otro de su clínica."""
    response = panel.post(CREATE_URL, {
        'patient': other_patient_a.pk,
        'procedures': [procedure_a.pk],
    })

    assert response.status_code == 200
    assert 'procedures' in response.context['form'].errors
    assert PatientInvoice.objects.count() == 0


@pytest.mark.django_db
def test_no_se_cuela_un_procedimiento_ya_facturado(panel, patient_a, issued_invoice_a, procedure_a):
    response = panel.post(CREATE_URL, {
        'patient': patient_a.pk,
        'procedures': [procedure_a.pk],
    })

    assert response.status_code == 200
    assert 'procedures' in response.context['form'].errors


@pytest.mark.django_db
def test_no_se_factura_a_un_paciente_de_otra_clinica(panel, patient_b):
    """`patient_b` es de clinic_b: ni siquiera aparece en el desplegable."""
    response = panel.post(CREATE_URL, {'patient': patient_b.pk})

    assert response.status_code == 200
    assert 'patient' in response.context['form'].errors
    assert PatientInvoice.objects.count() == 0


@pytest.mark.django_db
def test_el_paciente_fijado_por_la_url_no_se_puede_cambiar(panel, patient_a, other_patient_a, two_procedures):
    """Desde la ficha de alguien, el POST no puede redirigir la factura a otro."""
    response = panel.post(f'{CREATE_URL}?patient={patient_a.pk}', {
        'patient': other_patient_a.pk,        # intento de cambiazo
        'patient_locked': patient_a.pk,
        'procedures': [two_procedures[0].pk],
    })

    invoice = PatientInvoice.objects.get()
    assert response.status_code == 302
    assert invoice.patient_id == patient_a.pk


@pytest.mark.django_db
def test_el_formulario_ofrece_solo_lo_pendiente(panel, patient_a, procedure_a, two_procedures):
    response = panel.get(CREATE_URL, {'patient': patient_a.pk})

    ofrecidos = {p.pk for p in response.context['pending_procedures']}
    assert ofrecidos == {procedure_a.pk, *(p.pk for p in two_procedures)}


@pytest.mark.django_db
def test_fragmento_de_pendientes_por_htmx(panel, patient_a, procedure_a):
    url = reverse('billing:invoice-pending-procedures')
    response = panel.get(url, {'patient': patient_a.pk}, HTTP_HX_REQUEST='true')

    assert response.status_code == 200
    assert procedure_a.frozen_service_name in response.content.decode()


# ---------------------------------------------------------------------------
# Detalle y acciones
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_el_borrador_ensena_sus_procedimientos_vivos(panel, draft_invoice_a, procedure_a):
    draft_invoice_a.add_procedure(procedure_a)

    response = panel.get(reverse('billing:invoice-detail', args=[draft_invoice_a.pk]))

    assert response.context['procedures'] == [procedure_a]
    assert 'lines' not in response.context


@pytest.mark.django_db
def test_la_emitida_ensena_lines_y_no_procedimientos(panel, issued_invoice_a):
    """Regresión de la regla central: una emitida no relee sus procedimientos."""
    response = panel.get(reverse('billing:invoice-detail', args=[issued_invoice_a.pk]))

    assert response.context['lines'] == issued_invoice_a.lines
    assert 'procedures' not in response.context


@pytest.mark.django_db
def test_anadir_y_quitar_procedimientos_del_borrador(panel, draft_invoice_a, procedure_a):
    url = reverse('billing:invoice-procedure', args=[draft_invoice_a.pk])

    panel.post(url, {'action': 'add', 'procedure': procedure_a.pk})
    draft_invoice_a.refresh_from_db()
    assert draft_invoice_a.total == Decimal('50.00')

    panel.post(url, {'action': 'remove', 'procedure': procedure_a.pk})
    draft_invoice_a.refresh_from_db()
    assert draft_invoice_a.total == Decimal('0.00')
    procedure_a.refresh_from_db()
    assert procedure_a.invoice_id is None


@pytest.mark.django_db
def test_emitir_desde_el_detalle(panel, draft_invoice_a, procedure_a):
    draft_invoice_a.add_procedure(procedure_a)

    panel.post(reverse('billing:invoice-issue', args=[draft_invoice_a.pk]))

    draft_invoice_a.refresh_from_db()
    assert draft_invoice_a.is_issued
    assert draft_invoice_a.number


@pytest.mark.django_db
def test_anular_libera_los_procedimientos(panel, issued_invoice_a, procedure_a):
    panel.post(
        reverse('billing:invoice-void', args=[issued_invoice_a.pk]),
        {'reason': 'Procedimientos equivocados'},
    )

    issued_invoice_a.refresh_from_db()
    procedure_a.refresh_from_db()
    assert issued_invoice_a.is_void
    assert issued_invoice_a.void_reason == 'Procedimientos equivocados'
    assert procedure_a.invoice_id is None       # vuelve a estar pendiente
    assert issued_invoice_a.number              # el número se queda gastado


@pytest.mark.django_db
def test_anular_exige_motivo(panel, issued_invoice_a):
    panel.post(reverse('billing:invoice-void', args=[issued_invoice_a.pk]), {'reason': ''})

    issued_invoice_a.refresh_from_db()
    assert issued_invoice_a.is_issued           # sigue viva


@pytest.mark.django_db
def test_una_emitida_no_se_edita_ni_se_borra(panel, issued_invoice_a, procedure_a):
    detalle = reverse('billing:invoice-detail', args=[issued_invoice_a.pk])

    # Quitarle una línea: el modelo lo veta y la vista lo traduce a un mensaje.
    response = panel.post(
        reverse('billing:invoice-procedure', args=[issued_invoice_a.pk]),
        {'action': 'remove', 'procedure': procedure_a.pk},
    )
    assert response.status_code == 302 and response.url == detalle
    procedure_a.refresh_from_db()
    assert procedure_a.invoice_id == issued_invoice_a.pk

    # Borrarla: se anula, no se borra.
    panel.post(reverse('billing:invoice-delete', args=[issued_invoice_a.pk]))
    issued_invoice_a.refresh_from_db()
    assert issued_invoice_a.deleted_at is None


@pytest.mark.django_db
def test_eliminar_un_borrador_libera_sus_procedimientos(panel, draft_invoice_a, procedure_a):
    draft_invoice_a.add_procedure(procedure_a)

    response = panel.post(reverse('billing:invoice-delete', args=[draft_invoice_a.pk]))

    assert response.url == reverse('billing:invoice-list')
    procedure_a.refresh_from_db()
    assert procedure_a.invoice_id is None
    assert not PatientInvoice.objects.filter(pk=draft_invoice_a.pk).exists()


# ---------------------------------------------------------------------------
# Multi-tenancy
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_factura_de_otra_clinica_es_un_404(client, admin_user_b, issued_invoice_a):
    client.force_login(admin_user_b)

    for name in ('invoice-detail',):
        assert client.get(reverse(f'billing:{name}', args=[issued_invoice_a.pk])).status_code == 404
    assert client.post(reverse('billing:invoice-issue', args=[issued_invoice_a.pk])).status_code == 404
    assert client.post(reverse('billing:invoice-void', args=[issued_invoice_a.pk])).status_code == 404


# ---------------------------------------------------------------------------
# La ficha del paciente
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_pestana_de_procedimientos_ensena_la_factura(panel, patient_a, issued_invoice_a, procedure_a):
    response = panel.get(reverse('patients:tab-procedures', args=[patient_a.id]))

    body = response.content.decode()
    assert issued_invoice_a.number in body
    assert response.context['unbilled_count'] == 0


@pytest.mark.django_db
def test_la_pestana_ofrece_facturar_lo_pendiente(panel, patient_a, procedure_a):
    response = panel.get(reverse('patients:tab-procedures', args=[patient_a.id]))

    body = response.content.decode()
    assert response.context['unbilled_count'] == 1
    assert 'Sin facturar' in body
    assert f"{reverse('billing:invoice-create')}?patient={patient_a.id}" in body
