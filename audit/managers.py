"""Managers de solo inserción.

Un registro de auditoría que se puede editar o borrar no demuestra nada. Aquí se
cierran las tres puertas del ORM: `QuerySet.update()`, `QuerySet.delete()` y el
`save()`/`delete()` de una instancia ya persistida.

Lo que esto NO cubre (y no puede cubrir desde el ORM): `UPDATE`/`DELETE` en SQL
crudo y el acceso directo a la base de datos. La inmutabilidad de verdad se
consigue con permisos del rol de PostgreSQL o con un trigger `BEFORE UPDATE OR
DELETE`; esto es la primera barrera, no la única.
"""
from django.db import models

from audit.exceptions import AuditLogImmutable


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditLogImmutable(
            f'{self.model.__name__} es de solo inserción: no admite update().'
        )

    def delete(self):
        raise AuditLogImmutable(
            f'{self.model.__name__} es de solo inserción: no admite delete().'
        )

    def bulk_update(self, objs, fields, batch_size=None):
        raise AuditLogImmutable(
            f'{self.model.__name__} es de solo inserción: no admite bulk_update().'
        )


AppendOnlyManager = models.Manager.from_queryset(AppendOnlyQuerySet)


class AppendOnlyModel(models.Model):
    """Base para los registros de auditoría."""

    objects = AppendOnlyManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditLogImmutable(
                f'{type(self).__name__} es de solo inserción: no se puede modificar '
                f'un registro ya guardado (pk={self.pk}).'
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutable(
            f'{type(self).__name__} es de solo inserción: no se puede borrar '
            f'(pk={self.pk}).'
        )
