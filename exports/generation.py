"""Genera una exportación: de `pending` a `ready` (o `failed`).

Separado de la tarea de Celery para poder llamarlo igual desde un comando o un
test. La tarea (`exports/tasks.py`) solo busca la fila y llama aquí.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import timedelta

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from audit.context import ORIGIN_COMMAND, audit_context
from audit.mixins import log_access
from audit.models import AccessLog
from exports.builder import CHUNK_BYTES, build_export_zip
from exports.models import DataExport, retention_days
from exports.schema import PATIENT, Scope
from notifications.inbox import notify
from notifications.models import Notification

logger = logging.getLogger(__name__)

#: Texto que ve el usuario cuando algo falla. Genérico a propósito: el detalle va
#: al log del servidor, nunca a la fila (podría arrastrar datos de salud).
GENERIC_ERROR = 'No se pudo generar la exportación. Inténtalo de nuevo; si persiste, avisa a soporte.'
TIMEOUT_ERROR = 'La exportación tardó demasiado y se canceló. Inténtalo de nuevo.'


def _sha256_of(fileobj) -> tuple[str, int]:
    fileobj.seek(0)
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: fileobj.read(CHUNK_BYTES), b''):
        digest.update(chunk)
        size += len(chunk)
    fileobj.seek(0)
    return f'sha256:{digest.hexdigest()}', size


def _claim(export_id) -> DataExport | None:
    """Pasa la exportación de `pending` a `running`, o `None` si ya no toca.

    Con la fila bloqueada, dos ejecuciones de la misma tarea (reintento, doble
    encolado) no pueden generar el mismo ZIP a la vez.
    """
    with transaction.atomic():
        try:
            export = (
                DataExport.objects.select_for_update(of=('self',))
                .select_related('clinic', 'patient', 'requested_by')
                .get(pk=export_id)
            )
        except DataExport.DoesNotExist:
            return None
        if export.status != DataExport.Status.PENDING:
            return None
        export.mark_running()
        return export


def generate(export_id, *, pdf_renderer=None, soft_timeout_exc=()) -> DataExport | None:
    """Construye el ZIP, lo sube al bucket y deja la fila en su estado final."""
    export = _claim(export_id)
    if export is None:
        return None

    scope = Scope(clinic=export.clinic, patient=export.patient if export.scope == DataExport.Scope.PATIENT else None)
    uploaded_name = ''
    if pdf_renderer is None and scope.kind == PATIENT:
        from exports.pdf import render_patient_pdf

        pdf_renderer = render_patient_pdf
    try:
        with audit_context(
            user=export.requested_by,
            user_repr=export.requested_by_label,
            origin=ORIGIN_COMMAND,
        ):
            with tempfile.NamedTemporaryFile(prefix='export-', suffix='.zip') as tmp:
                result = build_export_zip(scope, tmp, pdf_renderer=pdf_renderer)
                tmp.flush()
                checksum, size = _sha256_of(tmp)
                export.file.save('export.zip', File(tmp), save=False)
                uploaded_name = export.file.name

            now = timezone.now()
            export.status = DataExport.Status.READY
            export.size_bytes = size
            export.checksum = checksum
            export.counts = {
                **result.counts,
                '_files': result.files,
                'files_unavailable': result.files['missing'] + result.files['unreadable'],
            }
            export.ready_at = now
            export.expires_at = now + timedelta(days=retention_days())
            export.error = ''
            export.save()

            log_access(
                action=AccessLog.Action.EXPORT,
                patient=export.patient,
                description=_description('Exportación generada', export, result.total_rows, result.files['total']),
                result_count=result.total_rows,
            )
    except soft_timeout_exc:
        _discard(export, uploaded_name, TIMEOUT_ERROR)
        raise
    except Exception:
        logger.exception('Falló la exportación %s', export_id)
        _discard(export, uploaded_name, GENERIC_ERROR)
        return export
    _notify(export, ready=True)
    return export


def _notify(export: DataExport, *, ready: bool) -> None:
    """Avisa a quien la pidió. Genérico: el push no lleva nombres ni datos de salud."""
    if export.requested_by_id is None:
        return
    from django.urls import reverse

    target = 'de la ficha' if export.scope == DataExport.Scope.PATIENT else 'de la clínica'
    try:
        notify(
            export.clinic,
            Notification.Kind.EXPORT_READY,
            url=reverse('exports:list'),
            object_id=export.pk,
            only_user=export.requested_by,
            detail=(f'Exportación {target} lista para descargar' if ready else f'La exportación {target} ha fallado'),
        )
    except Exception:
        # El aviso es un extra: no puede cambiar el resultado de la exportación.
        logger.exception('No se pudo avisar de la exportación %s', export.pk)


def _description(prefix: str, export: DataExport, rows: int, files: int) -> str:
    target = 'paciente' if export.scope == DataExport.Scope.PATIENT else 'clínica'
    return f'{prefix} ({target}): {rows} filas, {files} adjuntos'


def _discard(export: DataExport, uploaded_name: str, message: str) -> None:
    """Deja la exportación `failed` y retira el ZIP si ya se había subido."""
    if uploaded_name:
        try:
            export.file.storage.delete(uploaded_name)
        except Exception:
            logger.exception('No se pudo borrar el objeto %s de una exportación fallida', uploaded_name)
    export.file = ''
    export.size_bytes = None
    export.checksum = ''
    export.counts = {}
    export.status = DataExport.Status.FAILED
    export.error = message[:255]
    export.save()
    _notify(export, ready=False)
