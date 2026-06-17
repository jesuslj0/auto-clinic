from django import template

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
