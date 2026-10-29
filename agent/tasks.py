from celery import shared_task
from django.core.management import call_command


@shared_task
def purge_old_conversations():
    """Aplica la retención de conversaciones de cada clínica (`agent.retention`).

    Envuelve el comando `purge_conversations`, como `expire_appointment_holds`.
    Idempotente: sin hilos fuera de plazo no hace nada.
    """
    call_command('purge_conversations')
