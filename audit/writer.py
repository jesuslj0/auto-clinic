"""Escritura de registros de auditoría y política ante fallo."""
import logging

from audit.conf import is_fail_closed
from audit.exceptions import AuditLogImmutable, AuditWriteError

logger = logging.getLogger('audit')


def write_log(model, **fields):
    """Crea el registro aplicando la política de fallo configurada.

    Con `fail_closed` (por defecto) la excepción se propaga y tumba la operación
    auditada. Ojo con el alcance real de esa garantía: Django emite `post_save`
    FUERA de la transacción implícita de `Model.save()`, así que en autocommit
    la fila de dominio ya está escrita cuando llega aquí. Fail-closed asegura
    que la petición termina en error y que el fallo es visible, pero para que
    además sea atómico la operación tiene que ir dentro de un
    `transaction.atomic` (o activarse `ATOMIC_REQUESTS`). Está documentado en el
    README de la app.
    """
    try:
        return model.objects.create(**fields)
    except AuditLogImmutable:
        # Un bug nuestro, no un fallo de infraestructura: que salte siempre.
        raise
    except Exception as exc:
        logger.critical(
            'No se pudo escribir el registro de auditoría %s: %s',
            model.__name__,
            exc,
            exc_info=True,
            extra={'audit_fields': _safe_repr(fields)},
        )
        if is_fail_closed():
            raise AuditWriteError(
                f'No se pudo escribir el registro de auditoría ({model.__name__}). '
                f'La operación se aborta porque AUDIT_FAILURE_POLICY es fail_closed.'
            ) from exc
        return None


def _safe_repr(fields):
    """Traza del fallo sin volcar el diff, que puede llevar datos personales."""
    return {
        key: value
        for key, value in fields.items()
        if key in {'action', 'model_label', 'origin', 'path', 'method'}
    }
