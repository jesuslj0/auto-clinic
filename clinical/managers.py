"""Managers y utilidades de la capa clínica.

- `AppendOnlyInsertManager` / `AppendOnlyInsertQuerySet`: solo inserción, para la
  adenda. Es el mismo patrón que `audit.managers.AppendOnlyModel`, pero
  reimplementado aquí a propósito: `audit.registry.register()` rechaza cualquier
  subclase de `AppendOnlyModel` («no puede auditarse a sí mismo»), y la adenda
  SÍ debe auditarse. Así que comparte comportamiento pero no herencia.
- `next_history_number()`: correlativo de historia por clínica y año, con bloqueo
  de fila para que dos altas simultáneas no colisionen.
- `ClinicalAlertManager` / `ClinicalAlertQuerySet`: consultas de alertas, con la
  de las críticas activas de un paciente ya resuelta en un solo sitio.
"""
from django.db import models, transaction

from core.managers import ProtectedRecordError, SoftDeleteQuerySet


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


class ClinicalAlertQuerySet(SoftDeleteQuerySet):
    """Consultas de alertas clínicas. Encadenable como cualquier queryset."""

    def active(self):
        return self.filter(is_active=True)

    def critical(self):
        return self.filter(severity=self.model.Severity.CRITICAL)

    def for_patient(self, patient):
        return self.filter(patient=patient)

    def active_critical_for(self, patient):
        """Alertas críticas vigentes de un paciente, listas para pintar.

        Es la consulta que alimenta el bloque no descartable de la ficha, así que
        vive aquí y no repartida por las vistas: si mañana cambia qué cuenta como
        «crítica vigente», cambia en un sitio.

        El orden es el de presentación —la más reciente primero, y el `id` como
        desempate para que dos alertas del mismo instante no bailen entre
        peticiones—. Las desactivadas y las borradas lógicamente quedan fuera.
        """
        return self.for_patient(patient).active().critical().order_by('-created_at', '-id')


class ClinicalAlertManager(models.Manager.from_queryset(ClinicalAlertQuerySet)):
    """Manager por defecto de las alertas: oculta las borradas lógicamente.

    Se construye con `from_queryset` en vez de heredar de `SoftDeleteManager`
    por dos motivos: el `get_queryset()` de aquel instancia un
    `SoftDeleteQuerySet` a pelo y perdería estos métodos, y así los helpers
    (`active()`, `critical()`, `active_critical_for()`…) quedan disponibles
    también directamente sobre el manager. El contrato que importa —no ver los
    borrados— es el mismo.
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


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
