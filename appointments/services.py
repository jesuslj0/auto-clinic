from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from rest_framework import status as http_status
from rest_framework.exceptions import APIException

from appointments.models import (
    BLOCKING_STATUSES,
    DEFAULT_DURATION_MINUTES,
    Appointment,
    AppointmentStatusHistory,
    Professional,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)

# `BLOCKING_STATUSES` (qué estados ocupan un hueco) se define en models.py, que
# es de donde lo lee `find_overlap()`. Se reexporta aquí por comodidad: es la
# misma lista, no una copia. Nunca la redefinas.
__all__ = [
    'BLOCKING_STATUSES',
    'AppointmentDomainError',
    'NoProfessionalAvailable',
    'ProfessionalUnavailable',
    'create_appointment',
    'validate_appointment_update',
    'select_professional_for_appointment',
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
    require_online_booking: bool, exclude_pk=None,
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

    # El buffer se respeta alargando el hueco que ocupa la cita nueva, igual que
    # hace el motor de disponibilidad.
    margen = timedelta(minutes=professional.buffer_minutes)
    conflicto = Appointment.find_overlap(
        professional, scheduled_at, end_at + margen, exclude_pk=exclude_pk
    )
    if conflicto:
        return (
            f'El profesional ya tiene una cita confirmada que se solapa: '
            f'{conflicto.scheduled_at:%H:%M}–{conflicto.get_end_datetime():%H:%M}.'
        )

    return None


def _eligible_professionals(
    *, clinic, service, scheduled_at, end_at, require_online_booking: bool,
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
        ) is None
    ]


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
    - Si no se indica `status`, la cita nace en 'pending'.
    - Si no se indica `professional`, se auto-asigna el menos cargado que pueda
      atenderla. Si se indica, se valida con esos mismos criterios. En ninguno de
      los dos casos se crea una cita sin profesional: si no hay ninguno válido,
      la cita NO se crea y se lanza un error de dominio.
    - `require_online_booking` va atado al llamante, no al camino interno: la API
      y la reserva pública pasan True (con o sin `professional` explícito), el
      panel y el admin pasan False. Ver `ineligibility_reason`.
    """
    if end_at is None:
        duration = service.duration_minutes if service is not None else DEFAULT_DURATION_MINUTES
        end_at = scheduled_at + timedelta(minutes=duration)

    status = status or Appointment.Status.PENDING

    if professional is None:
        professional = select_professional_for_appointment(
            clinic=clinic, service=service, scheduled_at=scheduled_at, end_at=end_at,
            require_online_booking=require_online_booking,
        )
    else:
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

    def valor(campo):
        return changes[campo] if campo in changes else getattr(appointment, campo)

    professional = valor('professional')
    clinic = valor('clinic')
    service = valor('service')
    scheduled_at = valor('scheduled_at')
    status = valor('status')

    # Una cita cancelada o completada ya no ocupa agenda: no tiene sentido
    # revalidar su elegibilidad (y bloquearía cerrar citas antiguas).
    if status not in BLOCKING_STATUSES and status != Appointment.Status.PENDING:
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
