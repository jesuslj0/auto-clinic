from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.exceptions import APIException

from appointments.models import (
    BLOCKING_STATUSES,
    DEFAULT_DURATION_MINUTES,
    LIVE_STATUSES,
    Appointment,
    AppointmentStatusHistory,
    Professional,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)

# `BLOCKING_STATUSES` (qué estados ocupan un hueco) y `LIVE_STATUSES` (en cuáles
# la cita sigue viva) se definen en models.py, que es de donde las lee
# `find_overlap()`. Se reexportan aquí por comodidad: son las mismas, no copias.
# Nunca las redefinas.
__all__ = [
    'BLOCKING_STATUSES',
    'LIVE_STATUSES',
    'AppointmentDomainError',
    'NoProfessionalAvailable',
    'ProfessionalUnavailable',
    'InvalidTransition',
    'SlotUnavailable',
    'create_appointment',
    'lock_agenda',
    'validate_appointment_update',
    'select_professional_for_appointment',
    'register_patient_confirmation',
    'confirm_by_clinic',
    'cancel_appointment',
    'get_professional_availability',
    'get_clinic_available_slots',
]


# ---------------------------------------------------------------------------
# Errores de dominio
# ---------------------------------------------------------------------------

class AppointmentDomainError(APIException):
    """Error de negocio con cuerpo estable: {code, message, details}.

    `message` está redactado para que un cliente (el agente de WhatsApp) pueda
    mostrarlo tal cual: sin IDs internos ni detalles técnicos. Lo accionable va
    en `details`.
    """

    status_code = http_status.HTTP_400_BAD_REQUEST
    default_code = 'appointment_error'
    default_message = 'No se ha podido procesar la cita.'

    def __init__(self, message=None, details=None):
        super().__init__({
            'code': self.default_code,
            'message': message or self.default_message,
            'details': details or {},
        })


class NoProfessionalAvailable(AppointmentDomainError):
    default_code = 'no_professional_available'
    default_message = 'No hay ningún profesional disponible para ese servicio a esa hora.'


class ProfessionalUnavailable(AppointmentDomainError):
    default_code = 'professional_unavailable'
    default_message = 'El profesional indicado no está disponible para esa cita.'


class InvalidTransition(AppointmentDomainError):
    default_code = 'invalid_transition'
    default_message = 'La cita no admite ese cambio de estado.'


class SlotUnavailable(AppointmentDomainError):
    default_code = 'slot_unavailable'
    default_message = 'Ese hueco ya no está disponible. Elige otra hora, por favor.'


# ---------------------------------------------------------------------------
# Elegibilidad y selección de profesional
# ---------------------------------------------------------------------------

def _has_time_off(professional, scheduled_at, end_at) -> bool:
    return ProfessionalTimeOff.objects.filter(
        professional=professional,
        starts_at__lt=end_at,
        ends_at__gt=scheduled_at,
    ).exists()


DAY_NAMES = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def ineligibility_reason(
    *, professional, clinic, service, scheduled_at, end_at,
    require_online_booking: bool, exclude_pk=None, ignore_overlap: bool = False,
):
    """Motivo por el que un profesional NO puede atender esa cita, o None si sí puede.

    Fuente ÚNICA de la regla de elegibilidad: la leen tanto la auto-asignación
    como la validación del profesional que llega explícito en el payload. No
    dupliques estas comprobaciones en serializers, forms ni views.

    Un profesional es elegible si cumple TODO esto:
      1. Pertenece a la clínica de la cita.
      2. Presta el servicio pedido.
      3. Está activo (`is_active`).
      4. Acepta reservas online (`accepts_online_booking`) — SOLO si
         `require_online_booking`.
      5. Su horario cubre el tramo entero de la cita.
      6. No tiene una ausencia (`ProfessionalTimeOff`) que la pise.
      7. No tiene ya una cita solapada en un estado bloqueante
         (`BLOCKING_STATUSES`, vía `find_overlap()`), contando su `buffer_minutes`.

    `require_online_booking` lo fija QUIEN LLAMA, según de dónde venga la cita, y
    nunca según si el profesional venía explícito en el payload:
      - True  → API/agente y reserva pública (toda vía no autenticada por staff).
      - False → panel y admin. El staff sí puede asignar citas a un profesional
        que no acepta reservas online: el flag significa "el agente no puede
        ofrecerlo", no "está de baja" (para eso está `is_active`).

    Es lo ÚNICO que se relaja para el staff: todos los demás criterios se aplican
    igual por ambas vías. `services.py` no sabe nada de HTTP: recibe un booleano,
    no un `request`.

    `ignore_overlap` NO relaja nada de cara al usuario: sirve para preguntar "¿este
    profesional podría atenderla si el hueco estuviera libre?", y así distinguir
    "no hay nadie que preste ese servicio a esa hora" de "el hueco está pillado".
    Son dos mensajes distintos para el paciente. No lo uses para crear citas.
    """
    if professional.clinic_id != clinic.pk:
        return 'El profesional no pertenece a esta clínica.'

    if service is None or not professional.services.filter(pk=service.pk).exists():
        return 'El profesional seleccionado no ofrece el servicio indicado.'

    if not professional.is_active:
        return 'El profesional ya no está activo en la clínica.'

    if require_online_booking and not professional.accepts_online_booking:
        return 'El profesional no acepta reservas online.'

    tz = ZoneInfo(professional.clinic.timezone)
    local_start = scheduled_at.astimezone(tz)
    local_end = end_at.astimezone(tz)

    tramos = list(
        ProfessionalSchedule.objects.filter(
            professional=professional,
            day_of_week=local_start.weekday(),
            is_active=True,
        ).order_by('start_time')
    )
    if not tramos:
        return f'El profesional no trabaja los {DAY_NAMES[local_start.weekday()]}.'

    # Una cita que cruza la medianoche no cabe entera en ningún tramo del día.
    # Con jornada partida basta con que UN tramo la cubra: no puede pisar el parón.
    cabe = local_end.date() == local_start.date() and any(
        t.start_time <= local_start.time() and local_end.time() <= t.end_time
        for t in tramos
    )
    if not cabe:
        rangos = ', '.join(f'{t.start_time:%H:%M}–{t.end_time:%H:%M}' for t in tramos)
        return f'La cita debe estar dentro del horario del profesional ({rangos}).'

    if _has_time_off(professional, scheduled_at, end_at):
        return 'El profesional no está disponible a esa hora.'

    if ignore_overlap:
        return None

    conflicto = _overlapping_appointment(
        professional, scheduled_at, end_at, exclude_pk=exclude_pk
    )
    if conflicto:
        return (
            f'El profesional ya tiene una cita que se solapa: '
            f'{conflicto.scheduled_at:%H:%M}–{conflicto.get_end_datetime():%H:%M}.'
        )

    return None


def _overlapping_appointment(professional, scheduled_at, end_at, exclude_pk=None):
    """Cita que le pisa el tramo a este profesional, o None.

    El buffer se respeta alargando el hueco que ocupa la cita nueva, igual que
    hace el motor de disponibilidad.
    """
    margen = timedelta(minutes=professional.buffer_minutes)
    return Appointment.find_overlap(
        professional, scheduled_at, end_at + margen, exclude_pk=exclude_pk
    )


def _eligible_professionals(
    *, clinic, service, scheduled_at, end_at, require_online_booking: bool,
    ignore_overlap: bool = False,
) -> list:
    """Profesionales que pueden atender la cita, del más libre al más cargado.

    Orden: primero el que menos citas bloqueantes tiene ESE día (reparto de
    carga simple); a igualdad de carga, el de menor `id`, para que la elección
    sea determinista.
    """
    if service is None:
        return []

    day_start = scheduled_at.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    candidatos = (
        Professional.objects
        .filter(clinic=clinic, services=service)
        .select_related('clinic')
        .annotate(
            carga=Count(
                'appointments',
                filter=Q(
                    appointments__status__in=BLOCKING_STATUSES,
                    appointments__scheduled_at__gte=day_start,
                    appointments__scheduled_at__lt=day_end,
                ),
            )
        )
        .order_by('carga', 'id')
    )

    return [
        professional
        for professional in candidatos
        if ineligibility_reason(
            professional=professional, clinic=clinic, service=service,
            scheduled_at=scheduled_at, end_at=end_at,
            require_online_booking=require_online_booking,
            ignore_overlap=ignore_overlap,
        ) is None
    ]


def _slot_is_taken(
    *, clinic, service, scheduled_at, end_at, require_online_booking: bool, exclude_pk=None,
) -> bool:
    """¿El hueco está ocupado, teniendo la clínica quien pudiera atenderlo?

    Distingue los dos "no" que el paciente puede recibir, que no son el mismo:

      - "Ningún profesional presta ese servicio a esa hora" (nadie trabaja, nadie
        lo ofrece…) → `no_professional_available`.
      - "Alguien podría, pero ese hueco ya está cogido" → `slot_unavailable`, que
        invita a elegir otra hora.
    """
    candidatos = _eligible_professionals(
        clinic=clinic, service=service, scheduled_at=scheduled_at, end_at=end_at,
        require_online_booking=require_online_booking, ignore_overlap=True,
    )
    return any(
        _overlapping_appointment(professional, scheduled_at, end_at, exclude_pk=exclude_pk)
        for professional in candidatos
    )


def lock_agenda(*, clinic, service, professional=None):
    """Serializa las altas que compiten por el mismo hueco. Llamar dentro de atomic().

    OJO, esto no es lo que parece a primera vista: NO basta con bloquear las citas
    que ya existen en el tramo. Dos transacciones que insertan a la vez no ven la
    fila de la otra (aún no está commiteada) y no hay nada que bloquear, así que
    ambas pasarían la validación y ambas insertarían. Es un phantom read de manual.

    Lo que se bloquea es el RECURSO por el que compiten: la fila del profesional.
    Quien llega segundo espera, y cuando entra ya ve la cita del primero y la
    revalidación la rechaza con `slot_unavailable`.

    Con auto-asignación no sabemos aún el profesional, así que se bloquean todos
    los que prestan el servicio, ordenados por `pk` (orden estable = sin deadlocks).
    """
    candidatos = Professional.objects.select_for_update().filter(clinic=clinic)
    if professional is not None:
        candidatos = candidatos.filter(pk=professional.pk)
    elif service is not None:
        candidatos = candidatos.filter(services=service)

    list(candidatos.order_by('pk').values_list('pk', flat=True))


def select_professional_for_appointment(
    *, clinic, service, scheduled_at, end_at, require_online_booking: bool,
):
    """Elige el profesional que debe atender la cita.

    Devuelve el menos cargado ese día de entre los elegibles (ver
    `ineligibility_reason`). Lanza `NoProfessionalAvailable` si no hay ninguno.
    """
    elegibles = _eligible_professionals(
        clinic=clinic, service=service, scheduled_at=scheduled_at, end_at=end_at,
        require_online_booking=require_online_booking,
    )
    if not elegibles:
        raise NoProfessionalAvailable(details={
            'service': service.name if service else None,
            'scheduled_at': scheduled_at.isoformat(),
        })
    return elegibles[0]


def validate_professional_for_appointment(
    *, professional, clinic, service, scheduled_at, end_at,
    require_online_booking: bool, exclude_pk=None,
):
    """Comprueba que un profesional explícito puede atender la cita.

    Aplica exactamente los mismos criterios que la auto-asignación: `message`
    dice cuál ha fallado.
    """
    motivo = ineligibility_reason(
        professional=professional, clinic=clinic, service=service,
        scheduled_at=scheduled_at, end_at=end_at,
        require_online_booking=require_online_booking, exclude_pk=exclude_pk,
    )
    if motivo:
        raise ProfessionalUnavailable(motivo, details={
            'professional': str(professional),
            'scheduled_at': scheduled_at.isoformat(),
        })
    return professional


# ---------------------------------------------------------------------------
# Alta de citas
# ---------------------------------------------------------------------------

def create_appointment(
    *,
    clinic,
    scheduled_at,
    require_online_booking: bool,
    source: str,
    service=None,
    end_at=None,
    status=None,
    professional=None,
    actor=AppointmentStatusHistory.Actor.STAFF,
    actor_label='',
    **extra_fields,
):
    """
    Crea una cita y registra su estado inicial en el historial.

    Punto de entrada ÚNICO para dar de alta citas: lo comparten el
    AppointmentSerializer (API REST) y el AppointmentForm (panel web).

    - `scheduled_at` debe llegar ya en UTC (aware). La conversión desde el
      timezone de la clínica es responsabilidad de la capa que llama
      (serializer / form).
    - Si no se indica `end_at`, se calcula a partir de
      `service.duration_minutes` (30 min por defecto si no hay servicio).
    - Si no se indica `professional`, se auto-asigna el menos cargado que pueda
      atenderla. Si se indica, se valida con esos mismos criterios. En ninguno de
      los dos casos se crea una cita sin profesional: si no hay ninguno válido,
      la cita NO se crea y se lanza un error de dominio.
    - `require_online_booking` va atado al llamante, no al camino interno: la API
      y la reserva pública pasan True (con o sin `professional` explícito), el
      panel y el admin pasan False. Ver `ineligibility_reason`.

    `source` es OBLIGATORIO y explícito, igual que `require_online_booking`: lo
    fija quien llama, porque solo esa capa sabe de dónde viene la cita. Decide
    dos cosas, y por eso no puede tener un default silencioso:

      - En qué estado NACE. Una cita del staff nace `confirmed`: el staff ES la
        clínica, no hay nada que validar después. Las demás nacen `pending`, a la
        espera de que alguien de la clínica las valide.
      - Si lleva HOLD. Solo las que nacen pendientes de validación
        (`agent`, `booking`) caducan, y solo si la clínica configuró un TTL
        (`clinic.hold_ttl_minutes`; 0 = sin caducidad).
    """
    if end_at is None:
        duration = service.duration_minutes if service is not None else DEFAULT_DURATION_MINUTES
        end_at = scheduled_at + timedelta(minutes=duration)

    if status is None:
        status = (
            Appointment.Status.CONFIRMED
            if source == Appointment.Source.STAFF
            else Appointment.Status.PENDING
        )

    hold_expires_at = None
    ttl = clinic.hold_ttl_minutes
    if source != Appointment.Source.STAFF and status == Appointment.Status.PENDING and ttl:
        hold_expires_at = timezone.now() + timedelta(minutes=ttl)

    # Entre que `available-slots` ofreció el hueco y llega este POST han pasado
    # varios mensajes de WhatsApp: minutos. La disponibilidad de entonces no vale,
    # hay que revalidarla AHORA y con el recurso bloqueado. Sin esto, dos
    # conversaciones se cuelan por el mismo hueco y las dos reciben un "hecho".
    with transaction.atomic():
        lock_agenda(clinic=clinic, service=service, professional=professional)

        if professional is None:
            try:
                professional = select_professional_for_appointment(
                    clinic=clinic, service=service, scheduled_at=scheduled_at, end_at=end_at,
                    require_online_booking=require_online_booking,
                )
            except NoProfessionalAvailable:
                # Puede que sí hubiera quien la atendiera y lo único que falle sea
                # que el hueco ya está cogido: son dos mensajes distintos.
                if _slot_is_taken(
                    clinic=clinic, service=service, scheduled_at=scheduled_at, end_at=end_at,
                    require_online_booking=require_online_booking,
                ):
                    raise SlotUnavailable(details={'scheduled_at': scheduled_at.isoformat()})
                raise
        else:
            if _overlapping_appointment(professional, scheduled_at, end_at):
                raise SlotUnavailable(details={'scheduled_at': scheduled_at.isoformat()})
            validate_professional_for_appointment(
                professional=professional, clinic=clinic, service=service,
                scheduled_at=scheduled_at, end_at=end_at,
                require_online_booking=require_online_booking,
            )

        appointment = Appointment.objects.create(
            clinic=clinic,
            scheduled_at=scheduled_at,
            service=service,
            end_at=end_at,
            status=status,
            professional=professional,
            source=source,
            hold_expires_at=hold_expires_at,
            **extra_fields,
        )

        AppointmentStatusHistory.objects.create(
            appointment=appointment,
            from_status=None,
            to_status=status,
            actor=actor,
            actor_label=actor_label,
        )

    return appointment


# ---------------------------------------------------------------------------
# Transiciones de estado
# ---------------------------------------------------------------------------
#
# TODA escritura de `status` pasa por aquí, con su guardia explícita y su entrada
# en el historial. Nunca `.update()`, nunca un `appointment.status = ...` suelto
# en una view: eso es cómo se pierde la trazabilidad y cómo una cita acaba
# saltándose una regla que sí aplica el resto del código.


def _record_transition(appointment, from_status, *, actor, actor_label=''):
    AppointmentStatusHistory.objects.create(
        appointment=appointment,
        from_status=from_status,
        to_status=appointment.status,
        actor=actor,
        actor_label=actor_label,
    )


def register_patient_confirmation(appointment, *, at=None):
    """El paciente respondió "SÍ" al recordatorio.

    Registra el HECHO y NO toca `status`. El hueco ya estaba bloqueado antes de
    que contestara y sigue bloqueado después: no hay recurso que se libere ni que
    se ocupe, así que no hay transición. Ver el comentario del campo
    `patient_confirmed_at` en models.py: la asimetría con el "NO" (que sí cancela)
    es intencionada. Alguien va a querer "arreglarla" poniendo aquí un
    `status = CONFIRMED`. No lo hagas: `confirmed` significa "la clínica la tiene
    en firme", y el paciente no es la clínica.

    Idempotente: si ya había confirmado, se respeta la primera vez que lo hizo.
    """
    if appointment.status == Appointment.Status.CANCELLED:
        raise InvalidTransition('Esta cita fue cancelada y ya no puede confirmarse.')

    if appointment.patient_confirmed_at is None:
        appointment.patient_confirmed_at = at or timezone.now()

    # El flag lo escribía n8n a mano; lo mantenemos coherente desde aquí para no
    # dejar dos versiones de la misma verdad.
    appointment.reminder_responded = True
    appointment.save(
        update_fields=['patient_confirmed_at', 'reminder_responded', 'updated_at']
    )
    return appointment


def confirm_by_clinic(appointment, *, user=None):
    """La clínica valida la cita: `pending` → `confirmed`. Y el hold desaparece.

    Es la ÚNICA vía por la que una cita pasa a `confirmed`. Que el paciente diga
    que vendrá no la confirma (ver `register_patient_confirmation`).

    Una cita en firme no caduca, así que se le quita `hold_expires_at`.
    """
    if appointment.status == Appointment.Status.CONFIRMED:
        return appointment  # idempotente: validar dos veces no es un error

    if appointment.status != Appointment.Status.PENDING:
        raise InvalidTransition(
            f'Una cita en estado "{appointment.get_status_display()}" no puede confirmarse.'
        )

    # No se puede poner en firme un hueco que ya está ocupado por otra cita en
    # firme. La regla la decide `BLOCKING_STATUSES`, como en todo lo demás.
    if appointment.professional_id:
        conflicto = Appointment.find_overlap(
            appointment.professional,
            appointment.scheduled_at,
            appointment.get_end_datetime(),
            exclude_pk=appointment.pk,
        )
        if conflicto:
            raise ProfessionalUnavailable(
                f'{appointment.professional} ya tiene otra cita en ese tramo '
                f'({conflicto.scheduled_at:%H:%M}–{conflicto.get_end_datetime():%H:%M}).',
                details={'appointment': str(appointment.pk)},
            )

    previo = appointment.status
    appointment.status = Appointment.Status.CONFIRMED
    appointment.hold_expires_at = None
    appointment.save(update_fields=['status', 'hold_expires_at', 'updated_at'])

    _record_transition(
        appointment,
        previo,
        actor=AppointmentStatusHistory.Actor.STAFF,
        actor_label=(user.get_full_name() or user.email) if user else '',
    )
    return appointment


def cancel_appointment(
    appointment,
    *,
    actor=AppointmentStatusHistory.Actor.SYSTEM,
    actor_label='',
    cancelled_by=None,
):
    """Cancela la cita y libera el hueco. Idempotente."""
    if appointment.status == Appointment.Status.CANCELLED:
        return appointment

    previo = appointment.status
    appointment.status = Appointment.Status.CANCELLED
    appointment.cancelled_by = cancelled_by
    appointment.save(update_fields=['status', 'cancelled_by', 'updated_at'])

    _record_transition(appointment, previo, actor=actor, actor_label=actor_label)
    return appointment


def validate_appointment_update(appointment, changes: dict, *, require_online_booking: bool) -> dict:
    """Valida un cambio sobre una cita YA EXISTENTE y devuelve los campos a escribir.

    La invariante es "ninguna cita VIVA existe sin profesional", no solo "ninguna
    nace sin profesional": una cita no puede quedarse huérfana por un PATCH.

    Aplica la MISMA elegibilidad que el alta (`ineligibility_reason`), con
    `exclude_pk` para que la cita no se compare consigo misma. Lo usan el
    serializer (API y bulk-update) y el admin; ninguno de ellos duplica reglas.

    `changes` son los campos que el cliente quiere cambiar. Devuelve un dict con
    los que hay que escribir además de esos (hoy, el `end_at` recalculado).
    """
    if 'professional' in changes and changes['professional'] is None:
        raise ProfessionalUnavailable(
            'Una cita no puede quedarse sin profesional asignado.',
            details={'appointment': str(appointment.pk)},
        )

    # Campos que mueven o reservan el hueco. Un PATCH que no toca NINGUNO no cambia
    # la disponibilidad, así que no hay nada que revalidar: marcar
    # `reminder_24h_sent` (lo que hace n8n en cada recordatorio) o editar `notes`
    # no debe fallar porque el profesional dejara de ser elegible por otra causa
    # entretanto. Sin esto, n8n no podría marcar el recordatorio como enviado y lo
    # reintentaría en bucle.
    CAMPOS_DE_HUECO = {'scheduled_at', 'end_at', 'professional', 'service', 'status'}
    if not (CAMPOS_DE_HUECO & changes.keys()):
        return {}

    def valor(campo):
        return changes[campo] if campo in changes else getattr(appointment, campo)

    professional = valor('professional')
    clinic = valor('clinic')
    service = valor('service')
    scheduled_at = valor('scheduled_at')
    status = valor('status')

    # Una cita cancelada, completada o no_show ya no ocupa agenda: no tiene
    # sentido revalidar su elegibilidad (y bloquearía cerrar citas antiguas).
    if status not in LIVE_STATUSES:
        return {}

    # Si se mueve la hora sin decir la nueva duración, `end_at` se recalcula desde
    # el servicio. Arrastrar el `end_at` viejo daría un intervalo invertido y la
    # validación de solapamiento no encontraría nada.
    if 'end_at' in changes and changes['end_at']:
        end_at = changes['end_at']
    elif 'scheduled_at' in changes:
        duracion = service.duration_minutes if service else DEFAULT_DURATION_MINUTES
        end_at = scheduled_at + timedelta(minutes=duracion)
    else:
        end_at = appointment.end_at or appointment.get_end_datetime()

    if professional is None:
        raise ProfessionalUnavailable(
            'Una cita no puede quedarse sin profesional asignado.',
            details={'appointment': str(appointment.pk)},
        )

    # Mover una cita a un hueco ocupado es la misma carrera que crearla ahí: quien
    # llama debe hacerlo dentro de `atomic()` tras `lock_agenda()`. Ver
    # `AppointmentSerializer.update()`.
    if _overlapping_appointment(professional, scheduled_at, end_at, exclude_pk=appointment.pk):
        raise SlotUnavailable(details={'scheduled_at': scheduled_at.isoformat()})

    validate_professional_for_appointment(
        professional=professional, clinic=clinic, service=service,
        scheduled_at=scheduled_at, end_at=end_at,
        require_online_booking=require_online_booking,
        exclude_pk=appointment.pk,
    )

    return {'end_at': end_at}


# ---------------------------------------------------------------------------
# Motor de disponibilidad
# ---------------------------------------------------------------------------
#
# Toda la lógica de generación de huecos vive aquí. Las views solo orquestan
# (validan query params y serializan la respuesta); los serializers no calculan
# nada.
#
# Los `TimeField` de ProfessionalSchedule son hora LOCAL de la clínica. La
# conversión a instantes absolutos ocurre en `_schedule_windows()`, usando el
# timezone de la clínica sobre la fecha concreta que se consulta: es ahí donde
# se resuelve si ese "9:00" cae en +01:00 o en +02:00.


@dataclass
class ProfessionalAvailability:
    """Resultado del motor para un profesional y una fecha concretos."""

    works_this_day: bool
    slots: list  # datetimes aware, en el timezone de la clínica
    schedule_start: Optional[time] = None  # hora local, primer tramo del día
    schedule_end: Optional[time] = None    # hora local, último tramo del día


def _local_datetime(target_date: date_cls, hour: int, tz: ZoneInfo) -> datetime:
    """Instante correspondiente a `hour:00` hora local de `target_date`."""
    if hour >= 24:
        return datetime.combine(target_date + timedelta(days=1), time(0), tzinfo=tz)
    return datetime.combine(target_date, time(hour), tzinfo=tz)


def _schedule_windows(schedules, target_date: date_cls, tz: ZoneInfo) -> list:
    """Materializa tramos horarios locales como instantes absolutos de esa fecha."""
    return [
        (
            datetime.combine(target_date, schedule.start_time, tzinfo=tz),
            datetime.combine(target_date, schedule.end_time, tzinfo=tz),
        )
        for schedule in schedules
    ]


def _merge_windows(windows: list) -> list:
    """Funde tramos solapados o contiguos en tramos disjuntos y ordenados."""
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _clamp_windows(windows: list, lower: Optional[datetime], upper: Optional[datetime]) -> list:
    """Interseca los tramos con [lower, upper]. Los tramos que quedan vacíos se descartan."""
    clamped = []
    for start, end in windows:
        if lower is not None:
            start = max(start, lower)
        if upper is not None:
            end = min(end, upper)
        if start < end:
            clamped.append((start, end))
    return clamped


def _busy_intervals(appointments, buffer_minutes: Optional[int] = None) -> list:
    """Intervalos ocupados por citas confirmadas, con el buffer del profesional al final.

    `buffer_minutes` fuerza un buffer único (caso de un solo profesional); si no
    se indica, se usa el de cada profesional de cada cita.
    """
    intervals = []
    for appointment in appointments:
        if buffer_minutes is not None:
            buffer_after = buffer_minutes
        elif appointment.professional_id:
            buffer_after = appointment.professional.buffer_minutes
        else:
            buffer_after = 0
        end = appointment.get_end_datetime() + timedelta(minutes=buffer_after)
        intervals.append((appointment.scheduled_at, end))
    return intervals


def _overlaps_any(start: datetime, end: datetime, intervals: list) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end in intervals)


def _generate_slots(windows: list, blocked: list, duration: timedelta, step: timedelta) -> list:
    """Recorre cada tramo a pasos de `step` y devuelve los huecos que caben enteros."""
    slots = []
    for window_start, window_end in windows:
        current = window_start
        while current + duration <= window_end:
            if not _overlaps_any(current, current + duration, blocked):
                slots.append(current)
            current += step
    return sorted(slots)


def _time_off_intervals(professional, day_start: datetime, day_end: datetime) -> list:
    ausencias = ProfessionalTimeOff.objects.filter(
        professional=professional,
        starts_at__lt=day_end,
        ends_at__gt=day_start,
    )
    return [(time_off.starts_at, time_off.ends_at) for time_off in ausencias]


def get_professional_availability(
    professional,
    target_date: date_cls,
    duration_minutes: int,
    start_hour: Optional[int] = None,
    end_hour: Optional[int] = None,
) -> ProfessionalAvailability:
    """Huecos libres de un profesional en una fecha concreta.

    El rango del día lo define SIEMPRE el `ProfessionalSchedule` (todos los
    tramos activos de ese día de la semana, para soportar jornada partida).
    `start_hour` / `end_hour` son un filtro opcional que se interseca con ese
    horario, nunca la fuente de verdad del rango.

    Se descartan los huecos que se solapan con una cita CONFIRMADA (más el
    `buffer_minutes` del profesional), los que caen dentro de un
    `ProfessionalTimeOff`, y los que no caben enteros dentro de su tramo. Las
    citas 'pending' NO bloquean: ver `BLOCKING_STATUSES`.
    """
    if not professional.is_active:
        return ProfessionalAvailability(works_this_day=False, slots=[])

    schedules = list(
        ProfessionalSchedule.objects.filter(
            professional=professional,
            day_of_week=target_date.weekday(),
            is_active=True,
        ).order_by('start_time')
    )
    if not schedules:
        return ProfessionalAvailability(works_this_day=False, slots=[])

    tz = ZoneInfo(professional.clinic.timezone)
    windows = _merge_windows(_schedule_windows(schedules, target_date, tz))

    day_start = windows[0][0]
    day_end = windows[-1][1]

    windows = _clamp_windows(
        windows,
        _local_datetime(target_date, start_hour, tz) if start_hour is not None else None,
        _local_datetime(target_date, end_hour, tz) if end_hour is not None else None,
    )

    appointments = Appointment.objects.filter(
        professional=professional,
        scheduled_at__lt=day_end,
        scheduled_at__gte=day_start - timedelta(days=1),
        status__in=BLOCKING_STATUSES,
    ).select_related('service')

    blocked = _busy_intervals(appointments, buffer_minutes=professional.buffer_minutes)
    blocked += _time_off_intervals(professional, day_start, day_end)

    slots = _generate_slots(
        windows,
        blocked,
        duration=timedelta(minutes=duration_minutes),
        step=timedelta(minutes=professional.slot_granularity_minutes),
    )

    return ProfessionalAvailability(
        works_this_day=True,
        slots=slots,
        schedule_start=schedules[0].start_time,
        schedule_end=max(schedule.end_time for schedule in schedules),
    )


def get_clinic_available_slots(
    clinic,
    target_date: date_cls,
    duration_minutes: int,
    start_hour: int,
    end_hour: int,
) -> list:
    """Huecos libres de una clínica entera, sin distinguir profesional.

    El rango del día es la unión de los horarios de los profesionales activos de
    la clínica, intersecada con [start_hour, end_hour]. Si la clínica todavía no
    tiene ningún horario configurado, el rango es directamente [start_hour,
    end_hour] — así una clínica recién dada de alta sigue ofreciendo huecos.

    Una cita bloquea el hueco para toda la clínica (comportamiento previo, este
    endpoint no razona por profesional).
    """
    tz = ZoneInfo(clinic.timezone)
    lower = _local_datetime(target_date, start_hour, tz)
    upper = _local_datetime(target_date, end_hour, tz)

    schedules = list(
        ProfessionalSchedule.objects.filter(
            professional__clinic=clinic,
            professional__is_active=True,
            day_of_week=target_date.weekday(),
            is_active=True,
        ).order_by('start_time')
    )

    if schedules:
        windows = _clamp_windows(
            _merge_windows(_schedule_windows(schedules, target_date, tz)), lower, upper
        )
    else:
        windows = [(lower, upper)]

    if not windows:
        return []

    appointments = Appointment.objects.filter(
        clinic=clinic,
        scheduled_at__lt=windows[-1][1],
        scheduled_at__gte=windows[0][0] - timedelta(days=1),
        status__in=BLOCKING_STATUSES,
    ).select_related('service', 'professional')

    return _generate_slots(
        windows,
        _busy_intervals(appointments),
        duration=timedelta(minutes=duration_minutes),
        step=timedelta(minutes=duration_minutes),
    )
