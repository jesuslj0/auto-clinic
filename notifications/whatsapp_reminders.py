"""Recordatorio de cita por WhatsApp, enviado desde Django con una plantilla aprobada.

Sustituye al workflow `WA-Reminder-24h-Scheduler` de n8n. Fuera de las 24 h desde
el último mensaje del paciente solo se puede escribir con una plantilla, y un
recordatorio sale casi siempre fuera de ese plazo.

Qué plantilla: la que la clínica ha marcado con el uso «Recordatorio de cita
(24 h antes)» en Agente → Plantillas. Sin ninguna aprobada y marcada, la clínica
no envía recordatorios (es el interruptor). Qué dato de la cita va en cada
variable lo decide su `variable_map` (ver `appointments.reminder_fields`).

Qué estado deja, que es lo que lee el resto del sistema:

- `reminder_24h_sent` / `_at` en la cita. Con eso `pending-reminders` deja de
  devolverla, el recordatorio de 3 h puede seguir, y el orquestador de n8n sabe
  que el «sí» del paciente es la respuesta al recordatorio (consulta
  `reminder_24h_sent=true&reminder_responded=false&status=confirmed`).
- Un `ChatMessage` de tipo plantilla en el hilo, para que el staff lo vea.
"""

import logging
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from agent.models import ChatMessage, WhatsAppTemplate
from agent.services import get_or_create_session, record_unsent_template, send_automatic_template
from agent.whatsapp import WhatsAppError
from appointments.models import Appointment
from appointments.reminder_fields import MissingReminderField, resolve_values
from appointments.reminders import pending_reminder_queryset

logger = logging.getLogger(__name__)

#: Horas antes de la cita en las que se envía. Margen de 2 h para aguantar una
#: caída del worker sin perder el recordatorio; el flag evita enviarlo dos veces.
WINDOW_FROM_HOURS = 22
WINDOW_TO_HOURS = 24

#: Intentos fallidos tras los que se deja la cita en paz (número inválido,
#: plantilla pausada…): sin tope, se reintentaría cada 15 minutos hasta la cita.
MAX_ATTEMPTS = 3


def reminder_values(appointment, template):
    """Valores de la cita para las variables de `template`, en su orden."""
    return resolve_values(appointment, template.variable_map, template.variables)


def reminder_phone(appointment):
    if appointment.patient_id and appointment.patient.phone and not appointment.booked_for_other:
        return appointment.patient.phone
    return appointment.patient_phone


def _template_for(clinic, cache):
    """Plantilla de recordatorio de la clínica, o None si no puede enviar ninguno."""
    if clinic.pk not in cache:
        template = clinic.whatsapp_templates.filter(
            purpose=WhatsAppTemplate.Purpose.REMINDER_24H
        ).first()
        problem = template and (template.why_not_automatic() or template.reminder_map_problem())
        if problem:
            logger.warning(
                'Recordatorios de %s detenidos: la plantilla «%s» no sirve (%s)',
                clinic.clinic_id, template.name, problem,
            )
            template = None
        cache[clinic.pk] = template
    return cache[clinic.pk]


def _failed_attempts(appointment):
    return ChatMessage.objects.filter(
        clinic=appointment.clinic,
        status=ChatMessage.Status.FAILED,
        raw__kind='reminder_24h',
        raw__appointment_id=str(appointment.pk),
        # Una cita movida es otro recordatorio: los fallos de la hora vieja no cuentan.
        raw__scheduled_at=appointment.scheduled_at.isoformat(),
    ).count()


def send_reminder(appointment_id, templates=None):
    """Manda el recordatorio de 24 h de una cita. Devuelve qué pasó, en una palabra.

    `sent`, `failed`, `missing_data` (a la cita le falta un dato de la plantilla),
    `skipped` (otro worker la tiene, o ya está enviada), `no_template`, `no_phone`,
    `gave_up`.
    """
    templates = {} if templates is None else templates

    # El bloqueo de la fila evita el doble envío si dos ejecuciones se solapan
    # (una tarea que tarda más que el intervalo del beat). Tiene el coste de
    # tener una transacción abierta durante la llamada a Meta, pero es una sola
    # fila y la llamada tiene tope de 20 s. `of=('self',)`: el paciente es una
    # relación opcional y Postgres no bloquea el lado nulo de un JOIN externo.
    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('clinic', 'patient', 'professional__user')
            .filter(pk=appointment_id, reminder_24h_sent=False)
            .first()
        )
        if appointment is None:
            return 'skipped'

        clinic = appointment.clinic
        template = _template_for(clinic, templates)
        if template is None:
            return 'no_template'
        if not (clinic.whatsapp_phone_number_id and clinic.whatsapp_token):
            return 'no_template'

        phone = reminder_phone(appointment)
        if not phone:
            return 'no_phone'
        if _failed_attempts(appointment) >= MAX_ATTEMPTS:
            return 'gave_up'

        session = get_or_create_session(clinic, phone)
        extra_raw = {
            'kind': 'reminder_24h',
            'appointment_id': str(appointment.pk),
            'scheduled_at': appointment.scheduled_at.isoformat(),
        }
        try:
            values = reminder_values(appointment, template)
        except MissingReminderField as exc:
            # No se manda con un hueco vacío (Meta lo rechaza): queda en el hilo
            # como «No enviado» con el motivo y cuenta como intento, así que no se
            # insiste para siempre con una cita a la que le falta el dato.
            logger.warning('Recordatorio de la cita %s sin enviar: %s', appointment.pk, exc)
            record_unsent_template(session=session, template=template, reason=str(exc), extra_raw=extra_raw)
            return 'missing_data'

        try:
            send_automatic_template(
                session=session, template=template, params=values, extra_raw=extra_raw,
            )
        except (WhatsAppError, ValueError) as exc:
            # Se captura dentro de la transacción a propósito: así el mensaje
            # `failed` que dejó el envío se guarda y el staff ve que no llegó.
            logger.warning('Recordatorio de la cita %s no enviado: %s', appointment.pk, exc)
            return 'failed'

        appointment.reminder_24h_sent = True
        appointment.reminder_24h_sent_at = timezone.now()
        appointment.save(update_fields=['reminder_24h_sent', 'reminder_24h_sent_at', 'updated_at'])
        return 'sent'


def send_due_reminders(now=None):
    """Envía los recordatorios de 24 h que toquen ahora. Devuelve el recuento por resultado."""
    now = now or timezone.now()
    due = pending_reminder_queryset(
        Appointment.objects.all(),
        '24h',
        now=now,
        window=(now + timedelta(hours=WINDOW_FROM_HOURS), now + timedelta(hours=WINDOW_TO_HOURS)),
    ).order_by('scheduled_at')

    templates = {}
    results = Counter()
    for appointment_id in list(due.values_list('pk', flat=True)):
        results[send_reminder(appointment_id, templates)] += 1
    return dict(results)
