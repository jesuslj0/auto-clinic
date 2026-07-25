"""Los dos registros son de solo inserción.

Un log que se puede editar no prueba nada. Estas son las puertas del ORM; la
inmutabilidad frente a SQL crudo depende de los permisos de PostgreSQL y queda
fuera del alcance de la app.
"""
import pytest

from audit.exceptions import AuditLogImmutable
from audit.mixins import log_access
from audit.models import AccessLog, ChangeLog
from patients.models import Patient


@pytest.fixture
def change_log(db, clinic_a):
    Patient.objects.create(
        clinic=clinic_a, first_name="Immutable", last_name="Record", phone="+34600000099"
    )
    return ChangeLog.objects.filter(object_repr="Immutable Record").get()


@pytest.fixture
def access_log(db, patient_a):
    return log_access(action=AccessLog.Action.VIEW, patient=patient_a, obj=patient_a)


@pytest.mark.django_db
class TestChangeLogIsAppendOnly:
    def test_queryset_update_raises(self, change_log):
        with pytest.raises(AuditLogImmutable):
            ChangeLog.objects.filter(pk=change_log.pk).update(action='create')

    def test_queryset_delete_raises(self, change_log):
        with pytest.raises(AuditLogImmutable):
            ChangeLog.objects.filter(pk=change_log.pk).delete()

    def test_queryset_bulk_update_raises(self, change_log):
        change_log.action = 'create'
        with pytest.raises(AuditLogImmutable):
            ChangeLog.objects.bulk_update([change_log], ['action'])

    def test_saving_a_persisted_instance_raises(self, change_log):
        change_log.user_repr = 'otro'
        with pytest.raises(AuditLogImmutable):
            change_log.save()

    def test_instance_delete_raises(self, change_log):
        with pytest.raises(AuditLogImmutable):
            change_log.delete()

    def test_the_record_survives_every_attempt(self, change_log):
        for attempt in (
            lambda: ChangeLog.objects.filter(pk=change_log.pk).delete(),
            lambda: change_log.delete(),
        ):
            with pytest.raises(AuditLogImmutable):
                attempt()
        assert ChangeLog.objects.filter(pk=change_log.pk).exists()


@pytest.mark.django_db
class TestAccessLogIsAppendOnly:
    def test_queryset_update_raises(self, access_log):
        with pytest.raises(AuditLogImmutable):
            AccessLog.objects.filter(pk=access_log.pk).update(action='list')

    def test_queryset_delete_raises(self, access_log):
        with pytest.raises(AuditLogImmutable):
            AccessLog.objects.filter(pk=access_log.pk).delete()

    def test_saving_a_persisted_instance_raises(self, access_log):
        access_log.reason = 'reescrito'
        with pytest.raises(AuditLogImmutable):
            access_log.save()

    def test_instance_delete_raises(self, access_log):
        with pytest.raises(AuditLogImmutable):
            access_log.delete()


@pytest.mark.django_db
class TestUserDeletionKeepsTheLog:
    def test_deleting_the_user_keeps_the_dangling_id_and_the_identity(self, admin_user, clinic_a):
        """Borrar al usuario no toca su fila de auditoría.

        Los FK de los logs son `DO_NOTHING`: al morir el usuario, Django NO
        emite el `UPDATE ... SET user_id = NULL` (que el trigger de
        inmutabilidad rechazaría de todos modos). El FK queda apuntando a un id
        que ya no existe —permitido por `db_constraint=False`— y la identidad
        legible sobrevive en `user_repr`.
        """
        from audit.context import audit_context

        with audit_context(user=admin_user):
            patient = Patient.objects.create(
                clinic=clinic_a, first_name="Huella", last_name="Persistente", phone="+34600000098"
            )

        log = ChangeLog.objects.filter(object_repr="Huella Persistente").get()
        assert log.user_id == admin_user.pk

        user_pk = admin_user.pk
        admin_user.delete()

        log.refresh_from_db()
        # El id sigue ahí, ahora huérfano: el log no se reescribe ni al borrar
        # a quien lo generó.
        assert log.user_id == user_pk
        assert log.user_repr == 'Admin Alpha <admin@alpha.test>'


@pytest.mark.django_db
class TestAdminIsReadOnly:
    def test_changelog_list_is_reachable(self, admin_site_client, change_log):
        response = admin_site_client.get('/admin/audit/changelog/')
        assert response.status_code == 200

    def test_adding_a_record_by_hand_is_forbidden(self, admin_site_client):
        for url in ('/admin/audit/changelog/add/', '/admin/audit/accesslog/add/'):
            assert admin_site_client.get(url).status_code == 403

    def test_deleting_a_record_by_hand_is_forbidden(self, admin_site_client, change_log):
        response = admin_site_client.post(
            f'/admin/audit/changelog/{change_log.pk}/delete/', {'post': 'yes'}
        )
        assert response.status_code == 403
        assert ChangeLog.objects.filter(pk=change_log.pk).exists()

    def test_a_change_made_from_the_admin_is_marked_as_such(self, admin_site_client, patient_a):
        from audit.context import ORIGIN_ADMIN

        response = admin_site_client.post(
            f'/admin/patients/patient/{patient_a.pk}/change/',
            {
                'clinic': patient_a.clinic_id,
                'first_name': 'Editado',
                'last_name': patient_a.last_name,
                'email': patient_a.email,
                'phone': patient_a.phone,
                'date_of_birth': '',
                'notes': '',
            },
        )
        assert response.status_code in (200, 302)

        log = ChangeLog.objects.filter(
            model_label='patients.Patient',
            object_id=str(patient_a.pk),
            action=ChangeLog.Action.UPDATE,
        ).get()
        assert log.origin == ORIGIN_ADMIN
        assert log.changes['first_name'] == {'before': 'John', 'after': 'Editado'}
