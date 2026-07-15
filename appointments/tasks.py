from celery import shared_task
from django.core.management import call_command


@shared_task
def expire_appointment_holds():
    """Libera los huecos de las citas que el staff no validó a tiempo.

    Envuelve el management command del mismo nombre para que la lógica viva en un
    único sitio y se pueda lanzar también a mano. Idempotente: si no hay holds
    caducados, no hace nada.
    """
    call_command('expire_appointment_holds')
