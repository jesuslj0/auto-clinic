"""Datos del panel de control, salvo los económicos (esos viven en `billing.metrics`).

Cada función responde a UNA pregunta del día a día y devuelve datos ya listos
para pintar —diccionarios y listas, nunca querysets perezosos—, de modo que la
plantilla no calcula ni consulta nada. Todas reciben el usuario y acotan por su
clínica con `scope_to_clinic()`, la misma regla que el resto del panel.

**Aquí no hay datos clínicos.** Solo agenda, conversaciones y facturación:
recuentos, nombres de paciente que la agenda ya enseña y estados de cita. Por eso
el panel no lleva `AccessLog`. Si algún día se quiere señalar «paciente con
alerta clínica» en la agenda, hay que instrumentar la lectura primero.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.db.models import Count, Max, Min, Q
from django.db.models.functions import Coalesce, TruncDate, TruncWeek
from django.urls import reverse
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession, WorkflowError
from appointments.models import (
    Appointment,
    Professional,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)
from appointments.filters import UNRECORDED_WINDOW_DAYS, completed_without_procedure
from billing.filters import invoices_for, scope_to_clinic
from billing.metrics import _delta_pct, _previous_month_span
from patients.models import Patient

Status = Appointment.Status

#: Estados de una cita que ocupa agenda. El resto (cancelada, no asistió,
#: reagendada) libera o ya no cuenta como hueco ocupado.
ACTIVE_STATUSES = (Status.PENDING, Status.CONFIRMED)
BOOKED_STATUSES = (Status.PENDING, Status.CONFIRMED, Status.COMPLETED)

#: Un paciente sin cita futura cuya última visita fue hace más de esto se
#: considera «a recontactar». Seis meses: lo bastante largo para no molestar a
#: quien tiene revisiones semestrales y lo bastante corto para que no se pierda.
RECONTACT_DAYS = 183

#: La ventana de 24 h de WhatsApp se cierra a las 24 h del último mensaje del
#: paciente; el aviso salta cuando quedan menos de `WINDOW_WARNING_HOURS`.
WHATSAPP_WINDOW_HOURS = 24
WINDOW_WARNING_HOURS = 4

PERIODS = ('hoy', 'semana', 'mes')
PERIOD_LABELS = {'hoy': 'Hoy', 'semana': 'Semana', 'mes': 'Mes'}

WEEKDAYS_SHORT = ['L', 'M', 'X', 'J', 'V', 'S', 'D']


def _list_url(**params) -> str:
    """Enlace al listado de citas con los filtros que entiende `AppointmentFilters`."""
    base = {'desde': '', 'hasta': '', 'profesional': ''}
    base.update({key: value for key, value in params.items() if value is not None})
    return f"{reverse('appointments:list')}?{urlencode(base)}"


def greeting(now: datetime) -> str:
    """Saludo según la hora local: días hasta las 14, tardes hasta las 21, noches."""
    hour = timezone.localtime(now).hour
    if 6 <= hour < 14:
        return 'Buenos días'
    if 14 <= hour < 21:
        return 'Buenas tardes'
    return 'Buenas noches'


def week_bounds(day: date) -> tuple[date, date]:
    """Lunes y domingo de la semana de `day`."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _day_range(start: date, end: date):
    """Rango [inicio de `start`, inicio del día siguiente a `end`) en hora local."""
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, datetime.min.time()), tz),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), datetime.min.time()), tz),
    )


# ---------------------------------------------------------------------------
# 1. Alertas accionables
# ---------------------------------------------------------------------------

def dashboard_alerts(user, appointments, revenue: dict, today: date, now: datetime) -> list[dict]:
    """Cosas que requieren una acción, y solo las que existen ahora mismo.

    Cada alerta enlaza a la lista ya filtrada, así que el aviso y su remedio
    están a un clic. Si no hay nada que hacer la lista sale vacía y la plantilla
    no pinta la franja: un panel que siempre avisa de algo acaba ignorándose.

    Las cifras de cobro salen de `revenue` (ya calculadas por `billing.metrics`)
    para no definir «pendiente» en dos sitios.
    """
    from billing.models import PatientInvoice

    alerts: list[dict] = []

    # --- Chats ---------------------------------------------------------
    unread = scope_to_clinic(
        ConversationSession.objects.exclude(is_test=True).filter(unread_count__gt=0), user
    )
    window_start = now - timedelta(hours=WHATSAPP_WINDOW_HOURS)
    window_warning = now - timedelta(hours=WHATSAPP_WINDOW_HOURS - WINDOW_WARNING_HOURS)
    chats = unread.aggregate(
        total=Count('id'),
        oldest=Min('last_message_at'),
        closing=Count('id', filter=Q(last_message_at__gt=window_start, last_message_at__lte=window_warning)),
        human=Count('id', filter=Q(agent_paused=True)),
    )
    if chats['total']:
        oldest = chats['oldest']
        detail = f"La más antigua espera desde hace {_ago(now - oldest)}." if oldest else ''
        alerts.append({
            'key': 'chats',
            'tone': 'info',
            'count': chats['total'],
            'title': f"{chats['total']} chat{'s' if chats['total'] != 1 else ''} sin responder",
            'detail': detail,
            'url': f"{reverse('agent:chat-inbox')}?unread=1",
            'icon': 'M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 0 1-.825-.242m9.345-8.334a2.126 2.126 0 0 0-.476-.095 48.64 48.64 0 0 0-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0 0 11.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155',
        })
        if chats['closing']:
            alerts.append({
                'key': 'window',
                'tone': 'warning',
                'count': chats['closing'],
                'title': f"{chats['closing']} ventana{'s' if chats['closing'] != 1 else ''} de WhatsApp a punto de cerrarse",
                'detail': f"Quedan menos de {WINDOW_WARNING_HOURS} h para poder responder con texto libre.",
                'url': f"{reverse('agent:chat-inbox')}?unread=1",
                'icon': 'M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
            })
        if chats['human']:
            alerts.append({
                'key': 'human',
                'tone': 'warning',
                'count': chats['human'],
                'title': f"{chats['human']} paciente{'s' if chats['human'] != 1 else ''} esperando a una persona",
                'detail': 'El agente está en pausa en estas conversaciones: no contestará solo.',
                'url': f"{reverse('agent:chat-inbox')}?unread=1",
                'icon': 'M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z',
            })

    # --- Citas de hoy sin confirmar --------------------------------------
    pending_today = appointments.filter(status=Status.PENDING, scheduled_at__date=today).count()
    if pending_today:
        alerts.append({
            'key': 'pending_today',
            'tone': 'warning',
            'count': pending_today,
            'title': f"{pending_today} cita{'s' if pending_today != 1 else ''} de hoy sin confirmar",
            'detail': 'Confírmalas o libera el hueco.',
            'url': _list_url(desde=today.isoformat(), hasta=today.isoformat(), status='pending', sort='asc'),
            'icon': 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
        })

    # --- Citas atendidas sin procedimiento anotado ------------------------
    since = now - timedelta(days=UNRECORDED_WINDOW_DAYS)
    unrecorded = completed_without_procedure(appointments, since=since).count()
    if unrecorded:
        alerts.append({
            'key': 'unrecorded',
            'tone': 'warning',
            'count': unrecorded,
            'title': f"{unrecorded} cita{'s' if unrecorded != 1 else ''} completada{'s' if unrecorded != 1 else ''} sin procedimiento",
            'detail': f'De los últimos {UNRECORDED_WINDOW_DAYS} días: trabajo hecho que no se ha anotado ni se puede facturar.',
            'url': _list_url(
                status='completed', procedimiento='sin', desde=since.date().isoformat(), sort='desc'
            ),
            'icon': 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
        })

    # --- Facturación -----------------------------------------------------
    drafts = invoices_for(user).filter(status=PatientInvoice.Status.DRAFT).count()
    if revenue.get('unbilled_count'):
        alerts.append({
            'key': 'unbilled',
            'tone': 'info',
            'count': revenue['unbilled_count'],
            'title': f"{revenue['unbilled_count']} procedimiento{'s' if revenue['unbilled_count'] != 1 else ''} sin facturar",
            'detail': 'Trabajo hecho que todavía no cuelga de ninguna factura.',
            'url': reverse('billing:invoice-list'),
            'money': revenue['unbilled_amount'],
            'icon': 'M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z',
        })
    if revenue.get('unpaid_count'):
        alerts.append({
            'key': 'unpaid',
            'tone': 'danger',
            'count': revenue['unpaid_count'],
            'title': f"{revenue['unpaid_count']} factura{'s' if revenue['unpaid_count'] != 1 else ''} con saldo por cobrar",
            'detail': 'Emitidas y sin cobrar del todo, de cualquier fecha.',
            'url': f"{reverse('billing:invoice-list')}?cobro=pending",
            'money': revenue['pending_amount'],
            'icon': 'M2.25 18.75a60.07 60.07 0 0 1 15.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 0 1 3 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 0 0-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 0 1-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 0 0 3 15h-.75M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm3 0h.008v.008H18V10.5Zm-12 0h.008v.008H6V10.5Z',
        })
    if drafts:
        alerts.append({
            'key': 'drafts',
            'tone': 'info',
            'count': drafts,
            'title': f"{drafts} factura{'s' if drafts != 1 else ''} en borrador",
            'detail': 'Sin emitir: no cuentan como facturadas ni se pueden cobrar.',
            'url': f"{reverse('billing:invoice-list')}?status=draft",
            'icon': 'm16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125',
        })

    # --- Fallos del agente -----------------------------------------------
    since = now - timedelta(hours=24)
    workflow_errors = scope_to_clinic(WorkflowError.objects.filter(created_at__gte=since), user).count()
    failed_messages = scope_to_clinic(
        ChatMessage.objects.filter(
            direction=ChatMessage.Direction.OUTBOUND,
            status=ChatMessage.Status.FAILED,
            created_at__gte=since,
            session__is_test=False,
        ),
        user,
    ).count()
    failures = workflow_errors + failed_messages
    if failures:
        parts = []
        if workflow_errors:
            parts.append(f"{workflow_errors} error{'es' if workflow_errors != 1 else ''} del agente")
        if failed_messages:
            parts.append(f"{failed_messages} mensaje{'s' if failed_messages != 1 else ''} sin entregar")
        alerts.append({
            'key': 'failures',
            'tone': 'danger',
            'count': failures,
            'title': 'Fallos en las últimas 24 h',
            'detail': ' · '.join(parts).capitalize() + '.',
            'url': reverse('agent:chat-inbox'),
            'icon': 'M12 9v3.75m0-10.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.75c0 5.592 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.57-.598-3.75h-.152c-3.196 0-6.1-1.248-8.25-3.285Zm0 13.036h.008v.008H12v-.008Z',
        })

    return alerts


def _ago(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} días"


# ---------------------------------------------------------------------------
# 2. Agenda de hoy
# ---------------------------------------------------------------------------

def today_agenda(user, appointments, today: date, now: datetime, professional_id: int | None) -> list[dict]:
    """Las citas de hoy, cada una con la información que decide qué hacer.

    Marca la cita en curso y la siguiente, y suma indicadores de qué se sabe del
    paciente (primera visita, deuda, chat abierto) con **una consulta por
    indicador para toda la lista**, no una por fila.
    """
    queryset = appointments.filter(scheduled_at__date=today).select_related(
        'patient', 'service', 'professional__user'
    )
    if professional_id:
        queryset = queryset.filter(professional_id=professional_id)
    items = list(queryset)

    patient_ids = {item.patient_id for item in items if item.patient_id}
    day_start, _ = _day_range(today, today)

    had_visit = set(
        Appointment.objects.filter(
            patient_id__in=patient_ids, status=Status.COMPLETED, scheduled_at__lt=day_start
        ).values_list('patient_id', flat=True)
    )
    in_debt = set(
        invoices_for(user)
        .with_collection()
        .filter(patient_id__in=patient_ids, status='issued')
        .exclude(payment_state='paid')
        .values_list('patient_id', flat=True)
    )
    chats: dict[int, str] = {}
    for patient_id, session_id in scope_to_clinic(
        ConversationSession.objects.filter(patient_id__in=patient_ids, is_test=False)
        .order_by('-last_message_at')
        .values_list('patient_id', 'id'),
        user,
    ):
        chats.setdefault(patient_id, session_id)

    next_marked = False
    for item in items:
        end = item.get_end_datetime()
        active = item.status in ACTIVE_STATUSES
        item.is_current = active and item.scheduled_at <= now < end
        item.is_past = not active
        # Activa pero ya terminada: nadie la cerró. Se destaca, no se atenúa.
        item.is_overdue = active and end <= now
        item.is_next = False
        item.minutes_until = None
        if active and not item.is_current and item.scheduled_at > now and not next_marked:
            item.is_next = True
            item.minutes_until = max(int((item.scheduled_at - now).total_seconds() // 60), 0)
            next_marked = True
        item.is_first_visit = bool(item.patient_id) and item.patient_id not in had_visit
        item.has_debt = item.patient_id in in_debt
        item.chat_session_id = chats.get(item.patient_id)
        # La acción que toca: confirmar lo pendiente; completar lo confirmado que
        # ya empezó. Las demás pasan por «Gestionar».
        item.quick_action = None
        if item.status == Status.PENDING:
            item.quick_action = ('confirm', 'Confirmar')
        elif item.status == Status.CONFIRMED and item.scheduled_at <= now:
            item.quick_action = ('complete', 'Completar')
    return items


def clinic_professionals(user):
    """Profesionales que `user` puede ver, para el selector de la agenda."""
    return list(scope_to_clinic(Professional.objects.select_related('user'), user))


# ---------------------------------------------------------------------------
# 4a. Citas por estado
# ---------------------------------------------------------------------------

#: Orden, etiqueta y color sólido del segmento de cada estado. Los colores son
#: tokens del tema, así que la barra sigue al modo claro/oscuro.
_SEGMENTS = (
    (Status.PENDING, 'Pendientes', 'bg-warning', 'text-warning'),
    (Status.CONFIRMED, 'Confirmadas', 'bg-success', 'text-success'),
    (Status.COMPLETED, 'Completadas', 'bg-brand-500', 'text-brand-fg'),
    (Status.CANCELLED, 'Canceladas', 'bg-danger', 'text-danger'),
    (Status.NO_SHOW, 'No asistió', 'bg-content-faint', 'text-content-muted'),
    (Status.RESCHEDULED, 'Reagendadas', 'bg-info', 'text-info'),
)


def period_bounds(period: str, today: date) -> tuple[date, date]:
    if period == 'semana':
        return week_bounds(today)
    if period == 'mes':
        from appointments.filters import month_bounds

        return month_bounds(today)
    return today, today


def appointments_by_status(appointments, period: str, today: date) -> dict:
    """Reparto de las citas del periodo por estado, con el enlace a cada lista.

    Sustituye a las tarjetas sueltas de pendientes/canceladas, que mezclaban
    periodos (pendientes de cualquier fecha, canceladas del mes, citas de hoy).
    Aquí el periodo es uno y se elige.
    """
    if period not in PERIODS:
        period = 'hoy'
    start, end = period_bounds(period, today)
    counts = dict(
        appointments.filter(scheduled_at__date__gte=start, scheduled_at__date__lte=end)
        .values_list('status')
        .annotate(n=Count('id'))
        .order_by()
    )
    total = sum(counts.values())
    segments = []
    for status, label, bar, text in _SEGMENTS:
        count = counts.get(status, 0)
        # Rescheduled solo se enseña si hay: es ruido casi siempre.
        if status == Status.RESCHEDULED and not count:
            continue
        segments.append({
            'status': status.value,
            'label': label,
            'count': count,
            'pct': round(count / total * 100) if total else 0,
            'bar': bar,
            'text': text,
            'url': _list_url(
                desde=start.isoformat(), hasta=end.isoformat(), status=status.value,
                sort='asc' if status in ACTIVE_STATUSES else 'desc',
            ),
        })
    done = counts.get(Status.COMPLETED, 0)
    return {
        'period': period,
        'periods': [(key, PERIOD_LABELS[key]) for key in PERIODS],
        'start': start,
        'end': end,
        'total': total,
        'done': done,
        'done_pct': round(done / total * 100) if total else 0,
        'segments': [segment for segment in segments],
        'bar_segments': [segment for segment in segments if segment['count']],
        'all_url': _list_url(desde=start.isoformat(), hasta=end.isoformat()),
    }


# ---------------------------------------------------------------------------
# 4b. Nuevos pacientes
# ---------------------------------------------------------------------------

SPARK_WEEKS = 8


def new_patients_summary(user, today: date) -> dict:
    """Altas del mes, comparadas con el mismo tramo del anterior, y su tendencia."""
    from appointments.filters import month_bounds

    patients = scope_to_clinic(Patient.objects.all(), user)
    month_start, _ = month_bounds(today)
    current = patients.filter(created_at__date__gte=month_start, created_at__date__lte=today).count()

    previous_start, previous_end = _previous_month_span(today)
    previous = patients.filter(
        created_at__date__gte=previous_start, created_at__date__lte=previous_end
    ).count()
    delta = _delta_pct(current, previous)

    # Últimas 8 semanas (la actual incluida) con las semanas vacías a cero:
    # un hueco en la serie se lee como «ese dato no está», no como «cero altas».
    first_week = week_bounds(today)[0] - timedelta(weeks=SPARK_WEEKS - 1)
    since, _ = _day_range(first_week, first_week)
    raw = {
        row['week'].date() if isinstance(row['week'], datetime) else row['week']: row['n']
        for row in patients.filter(created_at__gte=since)
        .annotate(week=TruncWeek('created_at'))
        .values('week')
        .annotate(n=Count('id'))
        .order_by()
    }
    weeks = []
    for index in range(SPARK_WEEKS):
        monday = first_week + timedelta(weeks=index)
        weeks.append({'monday': monday, 'count': raw.get(monday, 0)})
    peak = max((week['count'] for week in weeks), default=0)
    for week in weeks:
        week['pct'] = max(round(week['count'] / peak * 100), 8) if peak else 8

    return {
        'month_start': month_start,
        'count': current,
        'previous': previous,
        'delta_pct': delta,
        'delta_abs': None if delta is None else abs(delta),
        'weeks': weeks,
        'has_trend': peak > 0,
        'latest': list(patients.order_by('-created_at')[:3]),
    }


# ---------------------------------------------------------------------------
# 3. Datos nuevos
# ---------------------------------------------------------------------------

def upcoming_days(appointments, today: date, days: int = 7) -> dict:
    """Citas en firme o por confirmar de los próximos `days` días, día a día."""
    end = today + timedelta(days=days - 1)
    counts = {
        row['day']: row['n']
        for row in appointments.filter(
            status__in=ACTIVE_STATUSES, scheduled_at__date__gte=today, scheduled_at__date__lte=end
        )
        .annotate(day=TruncDate('scheduled_at'))
        .values('day')
        .annotate(n=Count('id'))
        .order_by()
    }
    peak = max(counts.values(), default=0)
    series = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        count = counts.get(day, 0)
        series.append({
            'day': day,
            'weekday': WEEKDAYS_SHORT[day.weekday()],
            'count': count,
            'pct': max(round(count / peak * 100), 6) if peak else 6,
            'is_today': offset == 0,
            'url': _list_url(desde=day.isoformat(), hasta=day.isoformat(), sort='asc'),
        })
    return {'series': series, 'total': sum(counts.values()), 'peak': peak}


def week_occupancy(professionals, appointments, today: date) -> dict:
    """Horas reservadas frente a horas ofrecidas esta semana, por profesional.

    «Ofrecidas» son los tramos activos del horario semanal menos las ausencias
    (`ProfessionalTimeOff`) que los pisan; «reservadas», la duración de las citas
    que ocupan agenda. Es una lectura de capacidad, no una promesa: no contempla
    festivos ni huecos entre citas.
    """
    start, end = week_bounds(today)
    range_start, range_end = _day_range(start, end)
    tz = timezone.get_current_timezone()

    ids = [professional.pk for professional in professionals]
    schedules: dict[int, list] = {}
    for block in ProfessionalSchedule.objects.filter(professional_id__in=ids, is_active=True):
        schedules.setdefault(block.professional_id, []).append(block)
    time_off: dict[int, list] = {}
    for absence in ProfessionalTimeOff.objects.filter(
        professional_id__in=ids, starts_at__lt=range_end, ends_at__gt=range_start
    ):
        time_off.setdefault(absence.professional_id, []).append(absence)

    booked: dict[int, float] = {}
    day_offered = [0.0] * 7
    day_booked = [0.0] * 7
    for appointment in (
        appointments.filter(
            status__in=BOOKED_STATUSES,
            scheduled_at__gte=range_start,
            scheduled_at__lt=range_end,
            professional_id__in=ids,
        ).select_related('service')
    ):
        minutes = (appointment.get_end_datetime() - appointment.scheduled_at).total_seconds() / 60
        booked[appointment.professional_id] = booked.get(appointment.professional_id, 0) + minutes
        day_booked[timezone.localtime(appointment.scheduled_at).weekday()] += minutes

    rows = []
    total_offered = total_booked = 0.0
    for professional in professionals:
        offered = 0.0
        for block in schedules.get(professional.pk, []):
            day = start + timedelta(days=block.day_of_week)
            block_start = timezone.make_aware(datetime.combine(day, block.start_time), tz)
            block_end = timezone.make_aware(datetime.combine(day, block.end_time), tz)
            minutes = max((block_end - block_start).total_seconds() / 60, 0)
            for absence in time_off.get(professional.pk, []):
                overlap = min(block_end, absence.ends_at) - max(block_start, absence.starts_at)
                minutes -= max(overlap.total_seconds() / 60, 0)
            offered += max(minutes, 0)
            day_offered[block.day_of_week] += max(minutes, 0)
        done = booked.get(professional.pk, 0.0)
        total_offered += offered
        total_booked += done
        if not offered and not done:
            continue
        rows.append({
            'name': professional.user.get_full_name() or professional.user.email,
            'offered_h': round(offered / 60, 1),
            'booked_h': round(done / 60, 1),
            'pct': round(done / offered * 100) if offered else None,
            'bar': min(round(done / offered * 100), 100) if offered else 0,
        })
    rows.sort(key=lambda row: row['pct'] if row['pct'] is not None else -1, reverse=True)
    days = []
    for index in range(7):
        pct = round(day_booked[index] / day_offered[index] * 100) if day_offered[index] else None
        days.append({
            'weekday': WEEKDAYS_SHORT[index],
            'day': start + timedelta(days=index),
            'pct': pct,
            'bar': max(min(pct, 100), 4) if pct is not None else 0,
            'free_h': round(max(day_offered[index] - day_booked[index], 0) / 60, 1),
            'is_today': start + timedelta(days=index) == today,
            'off': not day_offered[index],
        })
    return {
        'days': days,
        'start': start,
        'end': end,
        'rows': rows,
        'offered_h': round(total_offered / 60, 1),
        'booked_h': round(total_booked / 60, 1),
        'pct': round(total_booked / total_offered * 100) if total_offered else None,
        'bar': min(round(total_booked / total_offered * 100), 100) if total_offered else 0,
    }


def month_quality(appointments, today: date) -> dict:
    """No-show y cancelación del mes, con el mes anterior al lado.

    El no-show se mide sobre las citas **resueltas** (completadas + no
    asistidas): una cita futura todavía no ha podido fallar. La cancelación, sobre
    todas las del periodo, porque cancelar es algo que se decide antes.
    """
    from appointments.filters import month_bounds

    def rates(start: date, end: date) -> dict:
        data = appointments.filter(
            scheduled_at__date__gte=start, scheduled_at__date__lte=end
        ).aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(status=Status.COMPLETED)),
            no_show=Count('id', filter=Q(status=Status.NO_SHOW)),
            cancelled=Count('id', filter=Q(status=Status.CANCELLED)),
        )
        resolved = data['completed'] + data['no_show']
        return {
            **data,
            'no_show_pct': round(data['no_show'] / resolved * 100) if resolved else None,
            'cancel_pct': round(data['cancelled'] / data['total'] * 100) if data['total'] else None,
        }

    month_start, month_end = month_bounds(today)
    previous_start, previous_end = _previous_month_span(today)
    current = rates(month_start, month_end)
    previous = rates(previous_start, previous_end)

    def delta(key):
        if current[key] is None or previous[key] is None:
            return None
        return current[key] - previous[key]

    def url(status):
        return _list_url(
            desde=month_start.isoformat(), hasta=month_end.isoformat(), status=status, sort='desc'
        )

    return {
        'month_start': month_start,
        'current': current,
        'previous': previous,
        # (etiqueta, valor, variación en puntos, enlace) listo para pintar.
        'rows': [
            ('No asistencia', current['no_show_pct'], delta('no_show_pct'), url('no_show')),
            ('Cancelación', current['cancel_pct'], delta('cancel_pct'), url('cancelled')),
        ],
    }


def top_services(appointments, today: date, limit: int = 5) -> list[dict]:
    """Servicios con más citas (no canceladas) del mes."""
    from appointments.filters import month_bounds

    month_start, month_end = month_bounds(today)
    rows = list(
        appointments.filter(
            status__in=BOOKED_STATUSES,
            scheduled_at__date__gte=month_start,
            scheduled_at__date__lte=month_end,
        )
        .annotate(label=Coalesce('service__name', 'service_name'))
        .values('label')
        .annotate(n=Count('id'))
        .order_by('-n', 'label')[:limit]
    )
    peak = rows[0]['n'] if rows else 0
    return [
        {
            'name': row['label'] or 'Sin servicio',
            'count': row['n'],
            'pct': max(round(row['n'] / peak * 100), 6) if peak else 0,
        }
        for row in rows
    ]


def booking_sources(appointments, today: date) -> dict:
    """De dónde llegan las citas creadas este mes: agente, reserva web o panel."""
    from appointments.filters import month_bounds

    month_start, _ = month_bounds(today)
    counts = dict(
        appointments.filter(created_at__date__gte=month_start, created_at__date__lte=today)
        .values_list('source')
        .annotate(n=Count('id'))
        .order_by()
    )
    total = sum(counts.values())
    tones = {'agent': 'bg-brand-500', 'booking': 'bg-info', 'staff': 'bg-content-faint'}
    rows = [
        {
            'label': label,
            'count': counts.get(value, 0),
            'pct': round(counts.get(value, 0) / total * 100) if total else 0,
            'bar': tones[value],
        }
        for value, label in Appointment.Source.choices
    ]
    return {'total': total, 'rows': rows}


def patients_to_recontact(user, now: datetime, limit: int = 5) -> dict:
    """Pacientes sin cita futura cuya última visita completada ya quedó lejos."""
    cutoff = now - timedelta(days=RECONTACT_DAYS)
    queryset = (
        scope_to_clinic(Patient.objects.all(), user)
        .annotate(
            last_visit=Max('appointments__scheduled_at', filter=Q(appointments__status=Status.COMPLETED)),
            upcoming=Count(
                'appointments',
                filter=Q(appointments__status__in=ACTIVE_STATUSES, appointments__scheduled_at__gte=now),
            ),
        )
        .filter(last_visit__lt=cutoff, upcoming=0)
    )
    return {
        'total': queryset.count(),
        'patients': list(queryset.order_by('-last_visit')[:limit]),
        'months': round(RECONTACT_DAYS / 30.5),
    }
