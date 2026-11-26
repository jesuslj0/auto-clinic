"""Icono y color por categoría de notificación, y una fecha corta («hace 1 h»).

Un único punto para el icono: lo usan el listado y el desplegable de la
campana. El toast (`static/js/notifications.js`) necesita lo mismo en
JavaScript —no hay forma de compartir esto en tiempo de ejecución del
navegador— así que lleva su propia copia de `ICONS` y de `short_timesince`;
**si se añade o cambia una categoría aquí, hay que tocar también ese
archivo.**
"""

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe

from notifications.models import Notification

register = template.Library()

Kind = Notification.Kind

#: kind -> (trazo del icono, color del trazo). Sin círculo ni borde: un icono
#: suelto, de contorno (outline), con el mismo color que el resto del panel usa
#: para esa categoría (ver CLAUDE.md «Front-end theming»).
ICONS = {
    Kind.APPOINTMENT_PENDING: (
        'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
        'text-info',
    ),
    Kind.APPOINTMENT_RESCHEDULED: (
        'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99',
        'text-warning',
    ),
    Kind.APPOINTMENT_CANCELLED: (
        'M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
        'text-danger',
    ),
    Kind.CHAT_NEW: (
        'M8.625 9.75a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z',
        'text-brand-fg',
    ),
    Kind.CHAT_MESSAGE: (
        'M8.625 9.75a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z',
        'text-brand-fg',
    ),
    Kind.MESSAGE_FAILED: (
        'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z',
        'text-danger',
    ),
    Kind.PATIENT_FORM_COMPLETED: (
        'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
        'text-success',
    ),
    Kind.EXPORT_READY: (
        'M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3',
        'text-success',
    ),
    Kind.TEMPLATE_STATUS: (
        'M10.34 15.84c-.688-.06-1.386-.09-2.09-.09H7.5a4.5 4.5 0 110-9h.75c.704 0 1.402-.03 2.09-.09m0 9.18c.253.962.584 1.892.985 2.783.447 1 1.77 1.267 2.47.473C14.08 18.544 15 16.313 15 13.5c0-2.813-.92-5.044-2.206-6.405-.701-.794-2.023-.527-2.47.473-.401.891-.732 1.821-.985 2.783m0 9.18a24.249 24.249 0 01-3.75 0',
        'text-info',
    ),
}
#: Un icono leído pierde el color: así el color es justo lo que distingue
#: «nuevo» de «visto» en el listado.
_READ_FG = 'text-content-faint'


@register.simple_tag
def notification_badge(kind, is_read=False):
    """Icono suelto (sin círculo ni borde), listo para pintar.

    Uso: `{% notification_badge n.kind n.is_read %}`, dentro de un contenedor
    `flex items-center` para que quede centrado a lo alto de la fila.
    """
    path, fg = ICONS.get(kind, ICONS[Kind.CHAT_MESSAGE])
    if is_read:
        fg = _READ_FG
    return mark_safe(
        f'<svg class="h-5 w-5 flex-shrink-0 self-center {fg}" fill="none" viewBox="0 0 24 24" '
        f'stroke-width="1.75" stroke="currentColor" aria-hidden="true">'
        f'<path stroke-linecap="round" stroke-linejoin="round" d="{path}" /></svg>'
    )


@register.filter
def short_timesince(value):
    """«hace 1 h», «hace 9 min», «hace 2 d»: la fecha de siempre, pero corta, junto al título."""
    if not value:
        return ''
    seconds = (timezone.now() - value).total_seconds()
    if seconds < 60:
        return 'ahora'
    minutes = seconds / 60
    if minutes < 60:
        return f'hace {int(minutes)} min'
    hours = minutes / 60
    if hours < 24:
        return f'hace {int(hours)} h'
    days = hours / 24
    if days < 7:
        return f'hace {int(days)} d'
    weeks = days / 7
    if weeks < 5:
        return f'hace {int(weeks)} sem'
    local = timezone.localtime(value)
    fmt = '%d/%m' if local.year == timezone.localtime().year else '%d/%m/%Y'
    return local.strftime(fmt)
