"""Qué citas toca recordar, en un solo sitio.

Lo usan `GET /api/appointments/pending-reminders/` y el envío de WhatsApp desde
Celery (`notifications.whatsapp_reminders`): si cada uno tuviera su copia de la
regla, acabarían recordando citas distintas.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from appointments.models import Appointment

#: Ventana por defecto de cada recordatorio, respecto a la hora de la cita.
DEFAULT_WINDOWS = {
    '24h': (timedelta(hours=23), timedelta(hours=25)),
    '3h': (timedelta(hours=2, minutes=30), timedelta(hours=3, minutes=30)),
}


def pending_reminder_queryset(queryset, kind='24h', *, now=None, window=None):
    """Citas de `queryset` a las que todavía hay que mandar el recordatorio `kind`.

    Solo se recuerdan las citas que la clínica tiene EN FIRME: una `pending` es
    una cita a la espera de que el staff la valide, y no tiene sentido pedirle al
    paciente que confirme algo que la clínica aún no ha aceptado.

    La regla es «que la clínica la haya aceptado», no «que esté ahora mismo en
    `confirmed`», y `rescheduled` obliga a notar la diferencia: ese estado colapsa
    dos situaciones que aquí NO son la misma. Una cita que estaba en firme y el
    agente movió de hora sigue siendo una cita aceptada, y dejar al paciente sin
    aviso porque nadie del staff ha vuelto a mirarla sería castigarle por un
    trámite interno. Una que nació `pending` y se movió antes de que nadie la
    validara no la ha aceptado la clínica todavía. El historial de estados ya sabe
    si esta cita pasó por `confirmed` alguna vez.

    `patient_confirmed_at` dice si el paciente ya respondió. Los flags
    `reminder_*` solo dicen qué se ha ENVIADO. Una reprogramación los limpia (ver
    `services.reschedule_appointment`), así que la hora nueva vuelve a avisar.

    `window` es `(desde, hasta)` en instantes absolutos; por defecto, el de
    `DEFAULT_WINDOWS` a partir de `now`.
    """
    now = now or timezone.now()
    if window is None:
        start, end = DEFAULT_WINDOWS['24h' if kind == '24h' else '3h']
        window = (now + start, now + end)

    if kind == '24h':
        pendiente_de_envio = {'reminder_24h_sent': False}
    else:  # 3h
        pendiente_de_envio = {'reminder_24h_sent': True, 'reminder_3h_sent': False}

    aceptada_por_la_clinica = Q(status=Appointment.Status.CONFIRMED) | Q(
        status=Appointment.Status.RESCHEDULED,
        status_history__to_status=Appointment.Status.CONFIRMED,
    )

    # `distinct()` no es decorativo: el JOIN contra el historial repite la cita
    # una vez por entrada que coincida, y una cita validada, movida y vuelta a
    # validar tiene dos `confirmed`. Sin él, el paciente recibe dos avisos.
    return queryset.filter(
        aceptada_por_la_clinica,
        scheduled_at__range=window,
        patient_confirmed_at__isnull=True,
        **pendiente_de_envio,
    ).distinct()
