"""Ajustes de la app de auditoría."""
from django.conf import settings

# La operación auditada se aborta si su registro no se puede escribir.
FAIL_CLOSED = 'fail_closed'
# La operación se completa y el fallo se anota en el logger `audit` a CRITICAL.
FAIL_OPEN = 'fail_open'


def failure_policy() -> str:
    """Política ante un fallo al escribir el registro.

    Por defecto `fail_closed`: son datos de salud, y un cambio que no se puede
    justificar después es peor que una operación que falla ahora. Se cambia con
    `AUDIT_FAILURE_POLICY` en settings.
    """
    return getattr(settings, 'AUDIT_FAILURE_POLICY', FAIL_CLOSED)


def is_fail_closed() -> bool:
    return failure_policy() == FAIL_CLOSED
