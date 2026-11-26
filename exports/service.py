"""Pedir una exportación: crear la fila, encolar la tarea y dejar constancia."""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from audit.mixins import log_access
from audit.models import AccessLog
from exports.models import DataExport
from exports.tasks import build_export

logger = logging.getLogger(__name__)

QUEUE_ERROR = 'No se pudo encolar la exportación. Inténtalo de nuevo en unos minutos.'


class ExportAlreadyRunning(Exception):
    """Ya hay una exportación en marcha del mismo ámbito."""


def _label(user) -> str:
    return (user.get_full_name() or user.email)[:255]


def request_export(*, clinic, user, patient=None) -> DataExport:
    """Crea la exportación (`pending`) y encola su generación.

    Solo puede haber una en marcha por paciente, y una de la clínica entera: lo
    impone la base con un constraint parcial, no una comprobación previa (que dos
    peticiones a la vez se saltarían).

    La tarea se encola al confirmarse la transacción. Si el broker no contesta, la
    fila se deja `failed` en vez de `pending`: una exportación que nunca arrancará
    y se queda «en cola» bloquearía las siguientes.
    """
    scope = DataExport.Scope.PATIENT if patient is not None else DataExport.Scope.CLINIC
    try:
        with transaction.atomic():
            export = DataExport.objects.create(
                clinic=clinic,
                requested_by=user,
                requested_by_label=_label(user),
                scope=scope,
                patient=patient,
                patient_label=f'{patient.first_name} {patient.last_name}'.strip()[:255] if patient else '',
            )
            log_access(
                action=AccessLog.Action.EXPORT,
                patient=patient,
                description=(
                    'Solicitud de exportación de la ficha de un paciente'
                    if patient is not None else 'Solicitud de exportación de toda la clínica'
                ),
            )
            transaction.on_commit(lambda: _enqueue(export.pk))
    except IntegrityError as exc:
        raise ExportAlreadyRunning from exc
    return export


def _enqueue(export_id) -> None:
    try:
        build_export.delay(str(export_id))
    except Exception:
        logger.exception('No se pudo encolar la exportación %s', export_id)
        export = DataExport.objects.filter(pk=export_id, status=DataExport.Status.PENDING).first()
        if export is not None:
            export.mark_failed(QUEUE_ERROR)
