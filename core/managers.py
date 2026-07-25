"""Managers y base para el borrado lógico.

En este proyecto hay una capa de datos que **no se borra físicamente jamás**: la
historia clínica y todo lo que cuelga de ella (Ley 41/2002, RGPD art. 9). El
borrado es siempre lógico —un sello de tiempo en `deleted_at`— y algunos
registros ni siquiera admiten eso.

Este módulo vive en `core` a propósito, para que futuras capas clínicas
(lesiones, procedimientos, consentimientos) lo reutilicen sin reescribirlo.

Puntos delicados que resuelve:

- El `delete()` que se suele olvidar es el del **QuerySet**: `qs.delete()` no
  pasa por `instance.delete()` y en Django hace un `DELETE` masivo sin señales.
  Aquí se sobreescribe para que recorra e invoque `instance.delete()`, de modo
  que el borrado lógico y la auditoría se respeten siempre.
- La fuente de verdad es **solo** `deleted_at`. No hay campo booleano
  `is_deleted` que pueda desincronizarse; se expone como property calculada en
  el modelo.
- Cada modelo puede vetar su borrado con `can_be_deleted()`. Un veto NO se
  degrada a borrado lógico: lanza excepción. Una nota clínica firmada no se
  borra de ninguna forma.
"""
from django.db import models


class ProtectedRecordError(Exception):
    """Se intentó borrar un registro que su modelo declara imborrable.

    Las apps clínicas la especializan (p. ej. `ProtectedClinicalRecord`) para dar
    un mensaje de dominio, pero el contrato es este: si `can_be_deleted()`
    devuelve `False`, ni el borrado lógico está permitido.
    """


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet cuyo `.delete()` hace borrado lógico, no físico."""

    def delete(self):
        """Borra lógicamente cada fila, respetando `can_be_deleted()`.

        No es un `UPDATE` masivo a propósito: recorre las instancias e invoca
        `instance.delete()`, para que la cascada de borrado lógico y las señales
        de auditoría se disparen igual que en un borrado uno a uno. Si algún
        registro veta su borrado, salta `ProtectedRecordError` y no se toca nada
        más (la iteración se aborta en el primer veto).
        """
        count = 0
        per_model = {}
        for obj in self:
            obj.delete()
            count += 1
            label = obj._meta.label
            per_model[label] = per_model.get(label, 0) + 1
        return count, per_model

    def hard_delete(self):
        """Borrado físico real. Sin uso en la capa clínica; reservado a la purga."""
        return super().delete()

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """Manager por defecto: oculta los registros borrados lógicamente."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class AllObjectsManager(models.Manager):
    """Manager que ve TODO, incluidos los borrados.

    Imprescindible: para la auditoría y para el derecho de acceso del paciente
    hay que poder llegar a un registro aunque esté borrado lógicamente. Es
    también el `base_manager` de los modelos con borrado lógico, para que las
    consultas internas de Django (y las señales de `audit`) nunca pierdan de
    vista una fila borrada.
    """

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)
