"""Caducidad de las exportaciones.

Un ZIP de exportación es una copia entera de datos de salud: no se queda en el
bucket más de `EXPORT_RETENTION_DAYS`. Pasado el plazo se borra el objeto y la
fila queda `expired`, como constancia de que se hizo.

Mismas reglas que la purga de chats (`agent/retention.py`):

- Una fila cada vez y con `save()`, nunca `queryset.update()`: el modelo está en la
  auditoría y un update en bloque se la saltaría.
- El objeto del bucket se borra al confirmarse la transacción, y un fallo al
  borrarlo se registra pero no impide marcar la fila.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from audit.context import ORIGIN_COMMAND, audit_context
from exports.models import DataExport

logger = logging.getLogger(__name__)

ACTOR = 'purga de exportaciones'
#: Una exportación «en marcha» más vieja que esto se dio por perdida (un worker
#: que murió a mitad). Por encima del límite duro de la tarea.
STALE_AFTER = timedelta(hours=2)
STALE_ERROR = 'La exportación no llegó a terminar. Vuelve a pedirla.'


@dataclass
class PurgeReport:
    expired: int = 0
    stale: int = 0
    objects_deleted: int = 0


def _delete_object(storage, name: str) -> None:
    try:
        storage.delete(name)
    except Exception:
        logger.exception('No se pudo borrar el objeto %s de una exportación caducada', name)


def purge_expired_exports(now=None) -> PurgeReport:
    now = now or timezone.now()
    report = PurgeReport()

    with audit_context(origin=ORIGIN_COMMAND, user_repr=ACTOR):
        due = DataExport.objects.filter(
            status=DataExport.Status.READY, expires_at__lte=now
        ).values_list('pk', flat=True)
        for pk in list(due):
            with transaction.atomic():
                export = DataExport.objects.select_for_update(skip_locked=True).filter(
                    pk=pk, status=DataExport.Status.READY, expires_at__lte=now
                ).first()
                if export is None:
                    continue
                name, storage = export.file.name, export.file.storage
                export.status = DataExport.Status.EXPIRED
                export.file = ''
                export.save()
                if name:
                    transaction.on_commit(lambda n=name, s=storage: _delete_object(s, n))
                    report.objects_deleted += 1
                report.expired += 1

        stuck = DataExport.objects.filter(
            status__in=DataExport.ACTIVE_STATUSES, updated_at__lte=now - STALE_AFTER
        ).values_list('pk', flat=True)
        for pk in list(stuck):
            with transaction.atomic():
                export = DataExport.objects.select_for_update(skip_locked=True).filter(
                    pk=pk, status__in=DataExport.ACTIVE_STATUSES, updated_at__lte=now - STALE_AFTER
                ).first()
                if export is None:
                    continue
                export.mark_failed(STALE_ERROR)
                report.stale += 1

    return report
