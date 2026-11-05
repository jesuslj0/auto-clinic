"""Emitir y anular son cosa de administradores; cobrar lo puede hacer cualquiera.

Emitir gasta un número de la serie correlativa y anular deja un documento sin
efecto para siempre: las dos son irreversibles, y solo las hacen los
administradores. Cobrar, en cambio, lo registra quien está en recepción: no cierra
ni deshace nada (no se puede cobrar más de lo debido ni contra un borrador) y queda
a nombre de quien lo registra. El personal administrativo (`staff`) puede además
preparar el trabajo —crear borradores, añadir y quitar procedimientos, tirar un
borrador— y leerlo todo.

Se vigila en las dos puntas: que la vista lo niegue aunque se llegue con la URL a
mano, y que la interfaz no ofrezca un botón que acabaría en un 403.
"""
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from billing.models import PatientInvoice, Payment, ReceiptSequence


# Un `Client` propio por sesión: la fixture `client` es una única instancia por
# test, y dos `force_login` sobre ella se pisarían (los tests que comparan lo que
# ve el staff con lo que ve el admin necesitan los dos a la vez).
@pytest.fixture
def staff_session(staff_user):
    session = Client()
    session.force_login(staff_user)
    return session


@pytest.fixture
def admin_session(admin_user):
    session = Client()
    session.force_login(admin_user)
    return session


def _post_pay(client, invoice, amount='10.00'):
    return client.post(
        reverse('billing:invoice-payment', args=[invoice.pk]),
        {'amount': amount, 'method': 'card'},
    )


# ---------------------------------------------------------------------------
# Las vistas lo niegan
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffCannotCloseAnything:
    def test_staff_cannot_issue(self, staff_session, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        response = staff_session.post(reverse('billing:invoice-issue', args=[draft_invoice_a.pk]))

        assert response.status_code == 403
        draft_invoice_a.refresh_from_db()
        assert draft_invoice_a.is_draft and draft_invoice_a.number is None

    def test_staff_cannot_void(self, staff_session, issued_invoice_a):
        response = staff_session.post(
            reverse('billing:invoice-void', args=[issued_invoice_a.pk]), {'reason': 'prueba'},
        )

        assert response.status_code == 403
        issued_invoice_a.refresh_from_db()
        assert issued_invoice_a.is_issued

    def test_the_admin_still_can(self, admin_session, draft_invoice_a, procedure_a):
        draft_invoice_a.add_procedure(procedure_a)

        admin_session.post(reverse('billing:invoice-issue', args=[draft_invoice_a.pk]))
        draft_invoice_a.refresh_from_db()
        assert draft_invoice_a.is_issued

        assert _post_pay(admin_session, draft_invoice_a).status_code == 302
        assert Payment.objects.get().amount == Decimal('10.00')

        admin_session.post(
            reverse('billing:invoice-void', args=[draft_invoice_a.pk]), {'reason': 'x'},
        )  # tiene cobros: se rechaza con un mensaje, no con un 403
        draft_invoice_a.refresh_from_db()
        assert draft_invoice_a.is_issued

    def test_an_anonymous_visitor_is_sent_to_login(self, client, issued_invoice_a):
        response = _post_pay(client, issued_invoice_a)

        assert response.status_code == 302
        assert '/login/' in response['Location']

    def test_the_agent_api_key_has_no_way_in(self, client, issued_invoice_a, clinic_a):
        """Todo esto es sesión: la clave del agente no abre ninguna de estas puertas."""
        response = client.post(
            reverse('billing:invoice-payment', args=[issued_invoice_a.pk]),
            {'amount': '10.00', 'method': 'card'},
            HTTP_AUTHORIZATION=f'Api-Key {clinic_a.agent_api_key}',
        )

        assert response.status_code in (302, 403)
        assert Payment.objects.count() == 0


# ---------------------------------------------------------------------------
# Lo que el staff sí hace
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestStaffCanCollect:
    def test_staff_can_register_a_payment(self, staff_session, issued_invoice_a):
        response = _post_pay(staff_session, issued_invoice_a)

        assert response.status_code == 302
        payment = Payment.objects.get()
        assert payment.amount == Decimal('10.00')
        assert payment.receipt_number.startswith('R-')

    def test_staff_can_open_the_payment_modal(self, staff_session, issued_invoice_a):
        response = staff_session.get(
            reverse('billing:invoice-payment-form', args=[issued_invoice_a.pk])
        )

        assert response.status_code == 200

    def test_staff_still_cannot_overpay(self, staff_session, issued_invoice_a):
        """Dejarlo abierto es seguro porque el modelo sigue mandando."""
        _post_pay(staff_session, issued_invoice_a, amount='999.00')

        assert Payment.objects.count() == 0

    def test_staff_cannot_collect_another_clinics_invoice(
        self, staff_session, clinic_b, patient_b
    ):
        other = PatientInvoice.objects.create(clinic=clinic_b, patient=patient_b)

        assert _post_pay(staff_session, other).status_code == 404

    def test_staff_cannot_collect_a_draft(self, staff_session, draft_invoice_a):
        _post_pay(staff_session, draft_invoice_a)

        assert Payment.objects.count() == 0


@pytest.mark.django_db
class TestStaffStillPreparesTheWork:
    def test_staff_can_read_the_list_and_the_detail(
        self, staff_session, issued_invoice_a, draft_invoice_a
    ):
        assert staff_session.get(reverse('billing:invoice-list')).status_code == 200
        assert staff_session.get(
            reverse('billing:invoice-detail', args=[issued_invoice_a.pk])
        ).status_code == 200

    def test_staff_can_create_a_draft_but_not_issue_it_from_the_form(
        self, staff_session, patient_a, procedure_a
    ):
        """Forzar `issue_now` en el POST no emite: el campo no existe para el staff."""
        response = staff_session.post(reverse('billing:invoice-create'), {
            'patient': patient_a.pk,
            'procedures': [procedure_a.pk],
            'issue_now': 'on',
        })

        assert response.status_code == 302
        invoice = PatientInvoice.objects.get()
        assert invoice.is_draft
        assert invoice.number is None
        assert invoice.procedures.count() == 1

    def test_the_admin_can_issue_from_the_form(self, admin_session, patient_a, procedure_a):
        admin_session.post(reverse('billing:invoice-create'), {
            'patient': patient_a.pk,
            'procedures': [procedure_a.pk],
            'issue_now': 'on',
        })

        assert PatientInvoice.objects.get().is_issued

    def test_staff_can_add_and_remove_procedures_and_drop_a_draft(
        self, staff_session, draft_invoice_a, procedure_a
    ):
        url = reverse('billing:invoice-procedure', args=[draft_invoice_a.pk])
        staff_session.post(url, {'action': 'add', 'procedure': procedure_a.pk})
        assert draft_invoice_a.procedures.count() == 1

        staff_session.post(url, {'action': 'remove', 'procedure': procedure_a.pk})
        assert draft_invoice_a.procedures.count() == 0

        staff_session.post(reverse('billing:invoice-delete', args=[draft_invoice_a.pk]))
        assert not PatientInvoice.objects.filter(pk=draft_invoice_a.pk).exists()


# ---------------------------------------------------------------------------
# La interfaz no ofrece lo que sería un 403
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTheInterfaceMatchesThePermissions:
    def test_the_create_form_has_no_issue_box_for_staff(self, staff_session):
        html = staff_session.get(reverse('billing:invoice-create')).content.decode()

        assert 'name="issue_now"' not in html
        assert 'Se guardará como borrador' in html

    def test_the_create_form_has_the_issue_box_for_the_admin(self, admin_session):
        html = admin_session.get(reverse('billing:invoice-create')).content.decode()

        assert 'name="issue_now"' in html

    def test_a_draft_shows_staff_no_issue_button(
        self, staff_session, admin_session, draft_invoice_a, procedure_a
    ):
        draft_invoice_a.add_procedure(procedure_a)
        url = reverse('billing:invoice-detail', args=[draft_invoice_a.pk])

        staff_html = staff_session.get(url).content.decode()
        assert reverse('billing:invoice-issue', args=[draft_invoice_a.pk]) not in staff_html
        assert 'Emitir factura' not in staff_html
        assert 'Solo un administrador puede emitirla' in staff_html

        admin_html = admin_session.get(url).content.decode()
        assert reverse('billing:invoice-issue', args=[draft_invoice_a.pk]) in admin_html

    def test_an_issued_invoice_shows_staff_no_void_control_but_does_show_payment(
        self, staff_session, admin_session, issued_invoice_a
    ):
        url = reverse('billing:invoice-detail', args=[issued_invoice_a.pk])
        void_url = reverse('billing:invoice-void', args=[issued_invoice_a.pk])
        pay_url = reverse('billing:invoice-payment', args=[issued_invoice_a.pk])

        staff_html = staff_session.get(url).content.decode()
        assert void_url not in staff_html
        assert 'Solo un administrador puede anularla' in staff_html
        assert pay_url in staff_html
        assert 'Registrar cobro' in staff_html

        admin_html = admin_session.get(url).content.decode()
        assert void_url in admin_html
        assert pay_url in admin_html
        assert 'Registrar cobro' in admin_html

    def test_the_list_shows_staff_the_collect_button(
        self, staff_session, issued_invoice_a
    ):
        form_url = reverse('billing:invoice-payment-form', args=[issued_invoice_a.pk])

        assert form_url in staff_session.get(reverse('billing:invoice-list')).content.decode()
