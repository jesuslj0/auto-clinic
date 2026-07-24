"""Contexto de petición.

Lo que se prueba aquí es que el contexto no sobrevive a la petición que lo creó.
Un `ContextVar` que se queda pegado atribuiría los cambios de un usuario a otro,
que en un log de accesos a datos de salud es peor que no tener log.
"""
import pytest
from django.urls import reverse

from audit.context import (
    EMPTY_CONTEXT,
    ORIGIN_COMMAND,
    audit_context,
    get_audit_context,
    resolve_actor,
)
from audit.models import AccessLog, ChangeLog
from patients.models import Patient


@pytest.mark.django_db
class TestRequestContextIsolation:
    def test_context_is_cleared_after_the_request(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        client.get(reverse('patients:detail', kwargs={'id': patient_a.pk}))

        assert get_audit_context() is EMPTY_CONTEXT
        assert resolve_actor() == (None, '', ORIGIN_COMMAND)

    def test_context_is_cleared_even_if_the_view_explodes(self, rf, admin_user):
        """El `finally` del middleware: una vista que revienta no deja restos."""
        from audit.middleware import AuditContextMiddleware

        def boom(request):
            raise RuntimeError('la vista ha reventado')

        middleware = AuditContextMiddleware(boom)
        request = rf.get('/pacientes/')
        request.user = admin_user

        with pytest.raises(RuntimeError):
            middleware(request)

        assert get_audit_context() is EMPTY_CONTEXT

    def test_context_does_not_leak_between_users(self, client, admin_user, staff_user, patient_a):
        url = reverse('patients:detail', kwargs={'id': patient_a.pk})

        client.force_login(admin_user)
        client.get(url)
        client.logout()

        client.force_login(staff_user)
        client.get(url)

        logs = list(AccessLog.objects.order_by('timestamp'))
        assert [log.user_id for log in logs] == [admin_user.pk, staff_user.pk]
        assert [log.user_repr for log in logs] == [
            'Admin Alpha <admin@alpha.test>',
            'Staff Alpha <staff@alpha.test>',
        ]

    def test_a_write_after_the_request_is_not_attributed_to_it(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        client.get(reverse('patients:list'))

        # Ya fuera de la petición: nadie debe firmar esto.
        patient = Patient.objects.create(
            clinic=clinic_a, first_name="Fuera", last_name="Contexto", phone="+34600000097"
        )
        log = ChangeLog.objects.filter(object_id=str(patient.pk)).get()
        assert log.user_id is None
        assert log.origin == ORIGIN_COMMAND


@pytest.mark.django_db
class TestExplicitContext:
    def test_audit_context_attributes_the_change(self, admin_user, clinic_a):
        """Lo que usaría un comando de gestión que sí sabe quién actúa."""
        with audit_context(user=admin_user, origin='command'):
            patient = Patient.objects.create(
                clinic=clinic_a, first_name="Manual", last_name="Comando", phone="+34600000096"
            )

        log = ChangeLog.objects.filter(object_id=str(patient.pk)).get()
        assert log.user_id == admin_user.pk
        assert log.user_repr == 'Admin Alpha <admin@alpha.test>'
        assert log.origin == ORIGIN_COMMAND

    def test_context_is_restored_after_the_block(self, admin_user):
        with audit_context(user=admin_user):
            assert get_audit_context().user == admin_user
        assert get_audit_context() is EMPTY_CONTEXT
