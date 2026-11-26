"""Un ZIP de exportación no se queda en el bucket más de lo debido."""
from datetime import timedelta

import pytest
from django.core.files.storage import storages
from django.utils import timezone

from audit.models import ChangeLog
from exports.models import DataExport
from exports.retention import STALE_AFTER, purge_expired_exports
from exports.tasks import build_export
from exports.tasks import purge_expired_exports as purge_task

pytestmark = pytest.mark.django_db


@pytest.fixture
def ready(file_a, clinic_a, admin_user, patient_a):
    export = DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
    )
    build_export.delay(str(export.pk))
    export.refresh_from_db()
    return export


def test_an_export_within_its_term_is_left_alone(ready):
    report = purge_expired_exports()

    assert report.expired == 0
    ready.refresh_from_db()
    assert ready.status == DataExport.Status.READY and storages['clinical_media'].exists(ready.file.name)


def test_a_past_due_export_loses_its_object_but_keeps_its_row(ready, django_capture_on_commit_callbacks):
    name = ready.file.name
    ready.expires_at = timezone.now() - timedelta(minutes=1)
    ready.save()

    with django_capture_on_commit_callbacks(execute=True):
        report = purge_expired_exports()

    assert report.expired == 1 and report.objects_deleted == 1
    ready.refresh_from_db()
    assert ready.status == DataExport.Status.EXPIRED and not ready.file
    assert ready.size_bytes and ready.checksum, 'queda constancia de lo que se generó'
    assert not storages['clinical_media'].exists(name)
    assert not ready.is_downloadable


def test_the_purge_is_audited_row_by_row(ready, django_capture_on_commit_callbacks):
    ready.expires_at = timezone.now() - timedelta(minutes=1)
    ready.save()
    ChangeLog.objects.all()  # noqa: B018

    with django_capture_on_commit_callbacks(execute=True):
        purge_expired_exports()

    change = ChangeLog.objects.filter(model_label='exports.DataExport').latest('id')
    assert change.user_repr == 'purga de exportaciones'
    assert 'status' in change.changes


def test_a_failure_deleting_the_object_does_not_block_the_purge(ready, monkeypatch,
                                                                 django_capture_on_commit_callbacks):
    ready.expires_at = timezone.now() - timedelta(minutes=1)
    ready.save()
    storage = storages['clinical_media']
    monkeypatch.setattr(type(storage), 'delete', lambda self, name: (_ for _ in ()).throw(OSError('R2 caído')))

    with django_capture_on_commit_callbacks(execute=True):
        report = purge_expired_exports()

    assert report.expired == 1
    ready.refresh_from_db()
    assert ready.status == DataExport.Status.EXPIRED


def test_a_stuck_export_is_given_up_and_stops_blocking(clinic_a, admin_user, patient_a):
    stuck = DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
        status=DataExport.Status.RUNNING,
    )
    DataExport.objects.filter(pk=stuck.pk).update(updated_at=timezone.now() - STALE_AFTER - timedelta(minutes=1))

    report = purge_expired_exports()

    assert report.stale == 1
    stuck.refresh_from_db()
    assert stuck.status == DataExport.Status.FAILED and stuck.error
    DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
    )


def test_a_recent_running_export_is_not_touched(clinic_a, admin_user, patient_a):
    DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
        status=DataExport.Status.RUNNING,
    )
    assert purge_expired_exports().stale == 0


def test_the_task_is_scheduled_nightly():
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE['purge-expired-exports']
    assert entry['task'] == 'exports.tasks.purge_expired_exports'
    assert purge_task() == {'expired': 0, 'stale': 0}
