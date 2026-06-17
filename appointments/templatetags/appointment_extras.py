from django import template
from django.utils import timezone

register = template.Library()

# Clases de color (Tailwind) por estado de cita.
STATUS_BADGE_CLASSES = {
    'pending': 'bg-amber-50 text-amber-700',
    'confirmed': 'bg-emerald-50 text-emerald-700',
    'completed': 'bg-brand-50 text-brand-700',
    'cancelled': 'bg-rose-50 text-rose-700',
    'rescheduled': 'bg-sky-50 text-sky-700',
    'no_show': 'bg-slate-100 text-slate-600',
}

# Borde + fondo para celdas del calendario.
STATUS_CELL_CLASSES = {
    'pending': 'border-amber-200 bg-amber-50',
    'confirmed': 'border-emerald-200 bg-emerald-50',
    'completed': 'border-brand-200 bg-brand-50',
    'cancelled': 'border-rose-200 bg-rose-50',
    'rescheduled': 'border-sky-200 bg-sky-50',
    'no_show': 'border-slate-300 bg-slate-100',
}

# Color sólido para puntos/indicadores (calendario).
STATUS_DOT_CLASSES = {
    'pending': 'bg-amber-500',
    'confirmed': 'bg-emerald-500',
    'completed': 'bg-brand-500',
    'cancelled': 'bg-rose-500',
    'rescheduled': 'bg-sky-500',
    'no_show': 'bg-slate-400',
}


@register.filter
def status_badge(status):
    """Devuelve las clases Tailwind de fondo/texto para el badge de un estado."""
    return STATUS_BADGE_CLASSES.get(status, 'bg-amber-50 text-amber-700')


@register.filter
def status_dot(status):
    """Devuelve la clase Tailwind de color sólido para el punto de un estado."""
    return STATUS_DOT_CLASSES.get(status, 'bg-amber-500')


@register.filter
def status_cell(status):
    """Devuelve las clases borde+fondo para una celda de calendario."""
    return STATUS_CELL_CLASSES.get(status, 'border-amber-200 bg-amber-50')


@register.filter
def local_minute(dt):
    """Minuto de inicio (0-59) en la zona horaria activa, para posicionar la tarjeta."""
    if not dt:
        return 0
    return timezone.localtime(dt).minute


@register.filter
def duration_minutes(appointment):
    """Duración de la cita en minutos (mínimo 5 para que la tarjeta sea visible)."""
    try:
        delta = appointment.get_end_datetime() - appointment.scheduled_at
        return max(int(delta.total_seconds() // 60), 5)
    except Exception:
        return 30
