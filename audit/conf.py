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


# ---------------------------------------------------------------------------
# Retención
# ---------------------------------------------------------------------------
#
# Los plazos no son libres (ver README): la Ley 41/2002 obliga a conservar la
# historia clínica un mínimo de 5 años, y el RGPD exige borrar lo que ya no
# hace falta. Los dos registros no comparten plazo: `AccessLog` crece mucho más
# rápido y su valor probatorio decae antes que el de `ChangeLog`.
#
# Los valores son en DÍAS y se cuentan sobre `timestamp`. Se sobrescriben con
# `AUDIT_RETENTION_DAYS` en settings, y por clínica con
# `AUDIT_RETENTION_DAYS_BY_CLINIC` (resuelto a través de `patient.clinic`).

# Clave interna de cada tipo de log para la configuración de retención.
RETENTION_CHANGE = 'change'
RETENTION_ACCESS = 'access'

DEFAULT_RETENTION_DAYS = {
    # ChangeLog: 6 años, por encima del mínimo legal de 5.
    RETENTION_CHANGE: 365 * 6,
    # AccessLog: 2 años.
    RETENTION_ACCESS: 365 * 2,
}


def retention_days() -> dict:
    """Plazos de retención por tipo de log, en días."""
    configured = getattr(settings, 'AUDIT_RETENTION_DAYS', {})
    return {**DEFAULT_RETENTION_DAYS, **configured}


def retention_days_by_clinic() -> dict:
    """Overrides por clínica: ``{clinic_id: {'change': N, 'access': M}}``.

    Vacío por defecto: la retención es solo por tipo, y el ajuste por clínica es
    opcional. La clínica de un registro se deduce de ``patient.clinic``; las
    filas sin paciente caen siempre bajo el plazo por defecto de su tipo.
    """
    return getattr(settings, 'AUDIT_RETENTION_DAYS_BY_CLINIC', {})
