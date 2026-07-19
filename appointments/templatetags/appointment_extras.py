from django import template
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()

# Icono (heroicons outline) por estado de cita. Fuente ÚNICA: badges y botones de
# acción tiran de aquí, así que el icono de "confirmed" es el mismo en el badge y
# en el botón "Confirmar". Cambia un path aquí y cambia en toda la app.
STATUS_ICON_PATHS = {
    'pending': 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',  # reloj
    'confirmed': 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z',  # check en círculo
    'completed': (  # sello con check
        'M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 '
        '11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 '
        '9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z'
    ),
    'cancelled': 'm9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z',  # x en círculo
    'rescheduled': (  # flechas de reprogramación
        'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 '
        '3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 '
        '3.182m0-4.991v4.99'
    ),
    'no_show': 'M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z',  # menos en círculo
}

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
def status_icon(status, css='h-4 w-4'):
    """SVG del icono de un estado, con las clases de tamaño que se le pasen.

    Uso: `{{ appointment.status|status_icon:"h-3.5 w-3.5" }}`. El color lo hereda
    del contenedor (`currentColor`), así que combina con `status_badge`.
    """
    path = STATUS_ICON_PATHS.get(status, STATUS_ICON_PATHS['pending'])
    return format_html(
        '<svg class="{}" fill="none" viewBox="0 0 24 24" stroke-width="1.8" '
        'stroke="currentColor" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        css, path,
    )


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
