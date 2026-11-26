"""El ciclo de una exportación: pending → running → ready / failed."""
import hashlib
import zipfile
from datetime import timedelta

import pytest
from django.core.files.storage import storages
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AccessLog, ChangeLog
from exports import generation
from exports.models import DataExport
from exports.tasks import build_export

pytestmark = pytest.mark.django_db


def stored_exports(prefix='exports'):
    """Todos los ficheros bajo `exports/` del bucket (los directorios vacíos no cuentan)."""
    storage = storages['clinical_media']
    try:
        directories, files = storage.listdir(prefix)
    except FileNotFoundError:
        return set()
    found = {f'{prefix}/{name}' for name in files}
    for directory in directories:
        found |= stored_exports(f'{prefix}/{directory}')
    return found


@pytest.fixture
def pending(file_a, clinic_a, admin_user, patient_a):
    return DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, requested_by_label=admin_user.email,
        scope=DataExport.Scope.PATIENT, patient=patient_a, patient_label='John Doe',
    )


def test_the_task_produces_a_ready_zip_in_the_private_bucket(pending):
    build_export.delay(str(pending.pk))

    export = DataExport.objects.get(pk=pending.pk)
    assert export.status == DataExport.Status.READY
    assert export.file.name.startswith('exports/') and export.file.name.endswith('.zip')
    assert storages['clinical_media'].exists(export.file.name)
    assert export.ready_at and export.expires_at
    assert export.expires_at - export.ready_at == timedelta(days=7)
    assert export.counts['patients'] == 1 and export.counts['_files']['ok'] == 3

    with export.file.open('rb') as stored:
        payload = stored.read()
    assert export.size_bytes == len(payload)
    assert export.checksum == 'sha256:' + hashlib.sha256(payload).hexdigest()
    assert 'manifest.json' in zipfile.ZipFile(export.file.open('rb')).namelist()


def test_the_key_says_nothing_about_the_patient(pending):
    build_export.delay(str(pending.pk))
    name = DataExport.objects.get(pk=pending.pk).file.name
    assert 'john' not in name.lower() and 'doe' not in name.lower()


def test_generating_leaves_an_access_log_in_the_requesters_name(pending, admin_user):
    build_export.delay(str(pending.pk))

    log = AccessLog.objects.filter(action=AccessLog.Action.EXPORT).latest('id')
    assert log.user == admin_user
    assert log.patient_id == pending.patient_id
    assert 'Exportación generada' in log.object_repr
    assert log.result_count > 0


def test_the_export_row_is_audited_without_the_bucket_key(pending):
    build_export.delay(str(pending.pk))

    changes = ChangeLog.objects.filter(model_label='exports.DataExport')
    assert changes.exists()
    key = DataExport.objects.get(pk=pending.pk).file.name
    assert all(key not in str(change.changes) for change in changes)


def test_it_only_runs_once(pending):
    build_export.delay(str(pending.pk))
    first = DataExport.objects.get(pk=pending.pk)
    key = first.file.name

    build_export.delay(str(pending.pk))

    assert DataExport.objects.get(pk=pending.pk).file.name == key


def test_a_failure_leaves_a_generic_error_and_no_object(pending, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('detalle con datos de salud: John Doe')

    monkeypatch.setattr(generation, 'build_export_zip', boom)

    build_export.delay(str(pending.pk))

    export = DataExport.objects.get(pk=pending.pk)
    assert export.status == DataExport.Status.FAILED
    assert export.error == generation.GENERIC_ERROR
    assert 'John' not in export.error
    assert not export.file


def test_a_failure_after_uploading_removes_the_orphan_object(pending, monkeypatch):
    original_save = DataExport.save
    calls = {'n': 0}

    def flaky_save(self, *args, **kwargs):
        calls['n'] += 1
        # 1ª: mark_running. 2ª: el paso a «ready», tras subir el ZIP: revienta.
        if self.status == DataExport.Status.READY and calls['n'] == 2:
            assert stored_exports(), 'el ZIP ya está subido cuando falla el guardado'
            raise RuntimeError('la base de datos se cayó')
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(DataExport, 'save', flaky_save)
    before = stored_exports()

    build_export.delay(str(pending.pk))

    export = DataExport.objects.get(pk=pending.pk)
    assert export.status == DataExport.Status.FAILED and not export.file
    assert stored_exports() == before


def test_a_pdf_that_fails_fails_the_export(pending, monkeypatch):
    def broken_pdf(scope):
        raise RuntimeError('weasyprint')

    original = generation.build_export_zip
    monkeypatch.setattr(
        generation, 'build_export_zip',
        lambda scope, dest, **kw: original(scope, dest, pdf_renderer=broken_pdf),
    )

    build_export.delay(str(pending.pk))

    assert DataExport.objects.get(pk=pending.pk).status == DataExport.Status.FAILED


def test_an_unknown_id_is_ignored():
    build_export.delay('00000000-0000-0000-0000-000000000000')


class TestOneAtATime:
    def test_a_second_active_export_of_the_same_patient_is_refused(self, pending, clinic_a, admin_user, patient_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            DataExport.objects.create(
                clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
            )

    def test_once_finished_another_can_be_requested(self, pending, clinic_a, admin_user, patient_a):
        build_export.delay(str(pending.pk))
        DataExport.objects.create(
            clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
        )

    def test_a_second_active_clinic_export_is_refused(self, clinic_a, admin_user):
        DataExport.objects.create(clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC)
        with pytest.raises(IntegrityError), transaction.atomic():
            DataExport.objects.create(clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC)

    def test_another_clinic_is_unaffected(self, clinic_a, clinic_b, admin_user, admin_user_b):
        DataExport.objects.create(clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.CLINIC)
        DataExport.objects.create(clinic=clinic_b, requested_by=admin_user_b, scope=DataExport.Scope.CLINIC)


class TestDownloadable:
    def test_ready_with_file_and_in_time(self, pending):
        build_export.delay(str(pending.pk))
        assert DataExport.objects.get(pk=pending.pk).is_downloadable

    def test_not_after_it_expires(self, pending):
        build_export.delay(str(pending.pk))
        export = DataExport.objects.get(pk=pending.pk)
        export.expires_at = timezone.now() - timedelta(seconds=1)
        assert not export.is_downloadable

    def test_not_while_pending(self, pending):
        assert not pending.is_downloadable
