"""Managers y utilidades de la capa clínica.

- `AppendOnlyInsertManager` / `AppendOnlyInsertQuerySet`: solo inserción, para la
  adenda. Es el mismo patrón que `audit.managers.AppendOnlyModel`, pero
  reimplementado aquí a propósito: `audit.registry.register()` rechaza cualquier
  subclase de `AppendOnlyModel` («no puede auditarse a sí mismo»), y la adenda
  SÍ debe auditarse. Así que comparte comportamiento pero no herencia.
- `next_history_number()`: correlativo de historia por clínica y año, con bloqueo
  de fila para que dos altas simultáneas no colisionen.
"""
from django.db import models, transaction

from core.managers import ProtectedRecordError


class AppendOnlyInsertQuerySet(models.QuerySet):
    """QuerySet de solo inserción: ni update, ni delete, ni bulk_update."""

    def update(self, **kwargs):
        raise ProtectedRecordError(
            f'{self.model.__name__} es de solo inserción: no admite update().'
        )

    def delete(self):
        raise ProtectedRecordError(
            f'{self.model.__name__} es de solo inserción: no admite delete().'
        )

    def bulk_update(self, objs, fields, batch_size=None):
        raise ProtectedRecordError(
            f'{self.model.__name__} es de solo inserción: no admite bulk_update().'
        )


AppendOnlyInsertManager = models.Manager.from_queryset(AppendOnlyInsertQuerySet)


def next_history_number(clinic) -> str:
    """Siguiente número de historia para `clinic`: ``HC-{año}-{correlativo}``.

    El correlativo se reinicia por año y es único dentro de la clínica. Se apoya
    en un contador con bloqueo de fila (`select_for_update`) para que dos altas
    concurrentes de paciente no obtengan el mismo número; el
    `unique_together=(clinic, number)` de la historia es la red de seguridad.

    Debe ejecutarse dentro de una transacción (lo garantiza la señal de creación
    de historia, que envuelve todo en `transaction.atomic`).
    """
    from django.utils import timezone

    from clinical.models import HistorySequence

    year = timezone.now().year
    with transaction.atomic():
        sequence, _ = (
            HistorySequence.objects.select_for_update()
            .get_or_create(clinic=clinic, year=year)
        )
        sequence.last_value = models.F('last_value') + 1
        sequence.save(update_fields=['last_value'])
        sequence.refresh_from_db(fields=['last_value'])
    return f'HC-{year}-{sequence.last_value:05d}'
