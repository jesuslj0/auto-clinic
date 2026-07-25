"""El comando `purge_audit_logs`.

Purgar auditoría es la única operación de borrado legítima, y tiene que cooperar
con las dos capas de inmutabilidad: usa SQL crudo (el ORM veta `delete()`) y pone
la válvula `audit.purga` con `SET LOCAL` para que el trigger deje pasar el
DELETE. Cada purga deja un meta-log para que el hueco no sea silencioso.

La retención se fuerza vía `settings` (0 días = todo está fuera de plazo) para no
depender de fabricar timestamps antiguos, que además no se pueden falsear con un
UPDATE: el trigger lo impediría.
"""
import pytest
from django.core.management import call_command

from audit.models import AccessLog, ChangeLog


def _make_old_rows():
    change = ChangeLog.objects.create(
        action=ChangeLog.Action.CREATE,
        model_label='patients.Patient',
        object_repr='cambio viejo',
    )
    access = AccessLog.objects.create(
        action=AccessLog.Action.VIEW,
        object_repr='acceso viejo',
    )
    return change, access


@pytest.mark.django_db
class TestPurgeCommand:
    def test_dry_run_deletes_nothing(self, settings):
        settings.AUDIT_RETENTION_DAYS = {'change': 0, 'access': 0}
        change, access = _make_old_rows()

        call_command('purge_audit_logs', '--dry-run')

        assert ChangeLog.objects.filter(pk=change.pk).exists()
        assert AccessLog.objects.filter(pk=access.pk).exists()
        # Un dry-run no purga, así que tampoco deja meta-log.
        assert not ChangeLog.objects.filter(model_label__startswith='audit.').exists()

    def test_purge_deletes_out_of_retention_rows(self, settings):
        settings.AUDIT_RETENTION_DAYS = {'change': 0, 'access': 0}
        change, access = _make_old_rows()

        call_command('purge_audit_logs')

        assert not ChangeLog.objects.filter(pk=change.pk).exists()
        assert not AccessLog.objects.filter(pk=access.pk).exists()

    def test_purge_leaves_a_meta_log_per_type(self, settings):
        settings.AUDIT_RETENTION_DAYS = {'change': 0, 'access': 0}
        _make_old_rows()

        call_command('purge_audit_logs')

        metas = ChangeLog.objects.filter(
            model_label__startswith='audit.', action=ChangeLog.Action.DELETE
        )
        assert set(metas.values_list('model_label', flat=True)) == {
            'audit.ChangeLog',
            'audit.AccessLog',
        }
        access_meta = metas.get(model_label='audit.AccessLog')
        assert access_meta.changes['purged_count'] == 1
        assert access_meta.changes['oldest_purged'] is not None

    def test_recent_rows_survive_the_default_retention(self, settings):
        # Sin override: rigen los plazos por defecto (años). Las filas recién
        # creadas están dentro de plazo y no se tocan.
        change, access = _make_old_rows()

        call_command('purge_audit_logs')

        assert ChangeLog.objects.filter(pk=change.pk).exists()
        assert AccessLog.objects.filter(pk=access.pk).exists()
        assert not ChangeLog.objects.filter(model_label__startswith='audit.').exists()
