"""Avisos de cita por WhatsApp: confirmada, cambiada y cancelada, con plantilla aprobada.

Hermano de `whatsapp_reminders`, pero por evento y no por reloj: el aviso sale
cuando la **clínica** hace algo con la cita (la confirma, la mueve, la cancela), no
cuando se acerca la hora. Fuera de las 24 h desde el último mensaje del paciente
solo se puede escribir con una plantilla, y casi siempre se está fuera.

Quién lo dispara: solo la clínica. Si lo pidió el paciente (por el agente o por el
enlace de la cita) ya recibió respuesta en el momento, y avisarle otra vez sería
duplicar. Por eso `queue_notice()` lo llama `appointments.services` solo cuando el
actor es el staff, y no hay aviso de cita cancelada por caducidad del hold.

Qué plantilla: la que la clínica marca con ese uso en Agente → Plantillas, una por
clínica y evento. Sin ninguna aprobada y marcada, ese aviso queda desactivado (es
el interruptor, igual que en el recordatorio). Los datos de cada variable salen de
`appointments.reminder_fields`: sin servicio ni nada clínico.

Cómo no falla en silencio ni se repite:

- Se encola con `transaction.on_commit` y se envía desde Celery: nunca se llama a
  Meta dentro de la petición del panel, y si el broker está caído la cita se guarda
  igualmente (se registra el error, no se propaga).
- Cada evento lleva un `event_id` que queda en `ChatMessage.raw`. Con él la tarea
  sabe si ese evento ya salió (no se manda dos veces si se reintenta o se redelivera)
  y cuántos intentos fallidos lleva (tope `MAX_ATTEMPTS`).
- Al enviar se comprueba que el aviso sigue siendo verdad: una cita confirmada y
  luego cancelada antes de que salga la tarea no recibe un «confirmada» tardío.
- Fila de la cita bloqueada durante el envío, fallo visible en el hilo y sin envío
  con una variable vacía, como en el recordatorio.
"""

import logging
import uuid
from datetime import datetime

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from agent.models import ChatMessage, WhatsAppTemplate
from agent.services import get_or_create_session, record_unsent_template, send_automatic_template
from agent.whatsapp import WhatsAppError
from appointments.models import Appointment
from appointments.reminder_fields import MissingReminderField, resolve_values
from notifications.whatsapp_reminders import MAX_ATTEMPTS, reminder_phone

logger = logging.getLogger(__name__)

Purpose = WhatsAppTemplate.Purpose

#: Qué plantilla usa cada aviso y cómo se llama en el hilo del paciente.
NOTICES = {
    'booking_confirmed': (Purpose.BOOKING_CONFIRMED, 'Aviso de cita confirmada'),
    'booking_changed': (Purpose.BOOKING_CHANGED, 'Aviso de cambio de cita'),
    'booking_cancelled': (Purpose.BOOKING_CANCELLED, 'Aviso de cita cancelada'),
}

#: Segundos que se espera antes de reintentar (multiplicado por el nº de intento).
RETRY_SECONDS = 60


def queue_notice(appointment, kind, *, previous_scheduled_at=None):
    """Encola el aviso `kind` de `appointment` para cuando se confirme la transacción.

    Lo llama `appointments.services` justo tras el cambio. Nunca lanza: un broker
    caído no puede impedir que la clínica confirme, mueva o cancele una cita.
    """
    if kind not in NOTICES:
        raise ValueError(f'Aviso desconocido: {kind}')

    payload = {
        'appointment_id': str(appointment.pk),
        'kind': kind,
        'event_id': uuid.uuid4().hex,
        # La hora de ESTE evento: si luego la cita se mueve, el aviso ya no vale.
        'scheduled_at': appointment.scheduled_at.isoformat(),
        'previous_at': previous_scheduled_at.isoformat() if previous_scheduled_at else None,
    }

    def enqueue():
        try:
            send_appointment_notice.delay(**payload)
        except Exception:  # noqa: BLE001 — el broker puede fallar de mil maneras
            logger.exception('No se pudo encolar el aviso %s de la cita %s', kind, appointment.pk)

    transaction.on_commit(enqueue)


def _template_for(clinic, purpose):
    """Plantilla de la clínica para `purpose`, o None si ese aviso no puede enviarse."""
    template = clinic.whatsapp_templates.filter(purpose=purpose).first()
    problem = template and (template.why_not_automatic() or template.reminder_map_problem())
    if problem:
        logger.warning(
            'Avisos «%s» de %s detenidos: la plantilla «%s» no sirve (%s)',
            purpose, clinic.clinic_id, template.name, problem,
        )
        return None
    return template


def notice_blocker(appointment, kind):
    """Por qué el aviso `kind` NO saldría para esta cita (en una frase para el staff), o '' si saldría.

    Para que el panel diga antes de guardar si el paciente va a enterarse, en vez
    de dejar al staff suponerlo. Mira lo mismo que `send_notice` salvo lo que solo
    se sabe al enviar (que Meta responda, que falte un dato de la plantilla).
    """
    purpose, label = NOTICES[kind]
    clinic = appointment.clinic
    if appointment.status not in (Appointment.Status.CONFIRMED, Appointment.Status.RESCHEDULED):
        return 'La cita aún no está confirmada, así que el paciente no tiene nada que corregir.'
    if not (clinic.whatsapp_phone_number_id and clinic.whatsapp_token):
        return 'La clínica no tiene WhatsApp conectado.'
    if _template_for(clinic, purpose) is None:
        return f'La clínica no tiene activo el «{label.lower()}» (Agente → Plantillas).'
    if not reminder_phone(appointment):
        return 'La cita no tiene teléfono de contacto.'
    return ''


def _still_true(appointment, kind, scheduled_at):
    """Si lo que va a decir el aviso sigue siendo cierto y útil cuando le toca salir."""
    if appointment.scheduled_at <= timezone.now():
        return False
    if kind == 'booking_cancelled':
        return appointment.status == Appointment.Status.CANCELLED
    # Confirmada o cambiada: viva y a la hora que el aviso dice, no a otra.
    return (
        appointment.status in (Appointment.Status.CONFIRMED, Appointment.Status.RESCHEDULED)
        and appointment.scheduled_at == scheduled_at
    )


def _attempts(clinic, event_id):
    """Cuántos intentos de este evento hay ya en el hilo: (enviados, fallidos)."""
    notices = ChatMessage.objects.filter(clinic=clinic, raw__notice_event=event_id)
    failed = notices.filter(status=ChatMessage.Status.FAILED).count()
    return notices.count() - failed, failed


def send_notice(appointment_id, kind, event_id, scheduled_at, previous_at=None):
    """Manda un aviso de cita. Devuelve qué pasó, en una palabra.

    `sent`, `failed` (Meta lo rechazó; se reintenta), `locked` (otro worker tiene
    la cita; se reintenta), `missing_data` (a la cita le falta un dato de la
    plantilla), `duplicate` (ese evento ya salió), `stale` (la cita cambió desde
    entonces o ya pasó), `no_template`, `no_phone`, `gave_up`, `gone`.
    """
    purpose, label = NOTICES[kind]
    scheduled_at = datetime.fromisoformat(scheduled_at)
    previous_at = datetime.fromisoformat(previous_at) if previous_at else None

    # El bloqueo de la fila evita el doble envío si dos ejecuciones se solapan.
    # `of=('self',)`: el paciente es una relación opcional y Postgres no bloquea
    # el lado nulo de un JOIN externo.
    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update(skip_locked=True, of=('self',))
            .select_related('clinic', 'patient', 'professional__user')
            .filter(pk=appointment_id)
            .first()
        )
        if appointment is None:
            # Sin fila: o la tiene otro worker (skip_locked) o ya no existe.
            return 'locked' if Appointment.objects.filter(pk=appointment_id).exists() else 'gone'

        if not _still_true(appointment, kind, scheduled_at):
            return 'stale'

        clinic = appointment.clinic
        template = _template_for(clinic, purpose)
        if template is None or not (clinic.whatsapp_phone_number_id and clinic.whatsapp_token):
            return 'no_template'

        phone = reminder_phone(appointment)
        if not phone:
            return 'no_phone'

        delivered, failed = _attempts(clinic, event_id)
        if delivered:
            return 'duplicate'
        if failed >= MAX_ATTEMPTS:
            return 'gave_up'

        session = get_or_create_session(clinic, phone)
        extra_raw = {
            'kind': kind,
            'notice_event': event_id,
            'appointment_id': str(appointment.pk),
            'scheduled_at': scheduled_at.isoformat(),
        }
        try:
            values = resolve_values(
                appointment, template.variable_map, template.variables, previous_at=previous_at
            )
        except MissingReminderField as exc:
            # No se manda con un hueco vacío (Meta lo rechaza): queda en el hilo
            # como «No enviado» con el motivo, y cuenta como intento para no
            # insistir con una cita a la que le falta el dato.
            logger.warning('Aviso %s de la cita %s sin enviar: %s', kind, appointment.pk, exc)
            record_unsent_template(
                session=session, template=template, reason=str(exc),
                extra_raw=extra_raw, label=label,
            )
            return 'missing_data'

        try:
            send_automatic_template(
                session=session, template=template, params=values, extra_raw=extra_raw,
            )
        except (WhatsAppError, ValueError) as exc:
            # Se captura dentro de la transacción a propósito: así el mensaje
            # `failed` que dejó el envío se guarda y el staff ve que no llegó.
            logger.warning('Aviso %s de la cita %s no enviado: %s', kind, appointment.pk, exc)
            return 'failed'
        return 'sent'


@shared_task(bind=True, max_retries=MAX_ATTEMPTS + 2)
def send_appointment_notice(self, *, appointment_id, kind, event_id, scheduled_at, previous_at=None):
    """Tarea de Celery de `send_notice`, con reintentos si Meta falla o la cita está ocupada."""
    result = send_notice(appointment_id, kind, event_id, scheduled_at, previous_at)
    if result in ('failed', 'locked') and self.request.retries < self.max_retries:
        raise self.retry(countdown=RETRY_SECONDS * (self.request.retries + 1))
    return result
