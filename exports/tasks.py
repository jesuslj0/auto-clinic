from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

# Una exportación de paciente tarda segundos; la de una clínica con miles de fotos,
# varios minutos. El límite blando deja a la tarea marcar la fila como fallida; el
# duro corta un proceso colgado.
SOFT_TIME_LIMIT = 25 * 60
HARD_TIME_LIMIT = 30 * 60


@shared_task(soft_time_limit=SOFT_TIME_LIMIT, time_limit=HARD_TIME_LIMIT)
def build_export(export_id):
    """Genera el ZIP de una `DataExport` en cola. Recibe el id, no el objeto."""
    from exports.generation import generate

    generate(str(export_id), soft_timeout_exc=SoftTimeLimitExceeded)


@shared_task
def purge_expired_exports():
    """Borra los ZIP caducados y da por fallidas las exportaciones que se quedaron colgadas."""
    from exports.retention import purge_expired_exports as purge

    report = purge()
    return {'expired': report.expired, 'stale': report.stale}
