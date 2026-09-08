"""Filtros y tags del panel de facturación.

Mismo planteamiento que `appointment_extras`: el color y el icono de un estado
salen de UN sitio, no de clases sueltas repartidas por las plantillas. Cambiar
aquí el badge de «anulada» lo cambia en la tabla, en las tarjetas del móvil y en
cualquier vista futura.
"""
from urllib.parse import urlencode

from django import template
from django.utils.html import format_html

from patients.templatetags.patient_extras import euros as _euros

register = template.Library()

# Un importe se escribe igual en toda la aplicación: `1.234,50 €`. Se reexporta
# el filtro de `patient_extras` en vez de copiarlo, para que no haya dos formatos
# de dinero que puedan divergir — la ficha del paciente y la factura tienen que
# decir el mismo número de la misma forma.
register.filter('euros', _euros)

# Clases (Tailwind) por estado de factura. Usan los tokens semánticos de
# `partials/_head_theme.html`, nunca la paleta cruda: así siguen al tema
# claro/oscuro sin un `dark:` por elemento.
INVOICE_STATUS_BADGE_CLASSES = {
    'draft': 'bg-muted-strong text-content-muted',
    'issued': 'bg-success-soft text-success',
    'void': 'bg-danger-soft text-danger',
}

# Icono (heroicons outline) por estado.
INVOICE_STATUS_ICON_PATHS = {
    # Lápiz: aún se está escribiendo.
    'draft': (
        'M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 '
        '4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z'
    ),
    # Check en círculo: emitida, cerrada.
    'issued': 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    # Círculo tachado: anulada. No desaparece, deja de valer.
    'void': 'M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636',
}


# Lo mismo para el estado de COBRO, que es otro eje: una factura emitida está
# siempre en los dos a la vez (emitida + parcial, por ejemplo). Los colores no
# son decorativos: rojo lo que no ha entrado, ámbar lo que ha entrado a medias,
# verde lo saldado.
PAYMENT_STATE_BADGE_CLASSES = {
    'unpaid': 'bg-danger-soft text-danger',
    'partial': 'bg-warning-soft text-warning',
    'paid': 'bg-success-soft text-success',
}

PAYMENT_STATE_ICON_PATHS = {
    # Billete tachado: no ha entrado nada.
    'unpaid': (
        'M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 '
        '19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 '
        '4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 '
        '5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228'
        '-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88'
    ),
    # Reloj: entró parte, queda pendiente.
    'partial': 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
    # Check en círculo: saldada.
    'paid': 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}


@register.filter
def payment_state_badge(state):
    """Clases de fondo/texto del badge de un estado de cobro."""
    return PAYMENT_STATE_BADGE_CLASSES.get(state, 'bg-muted-strong text-content-muted')


@register.filter
def payment_state_icon(state, css='h-4 w-4'):
    """SVG del icono de un estado de cobro. Mismo contrato que el de factura."""
    path = PAYMENT_STATE_ICON_PATHS.get(state, PAYMENT_STATE_ICON_PATHS['unpaid'])
    return format_html(
        '<svg class="{}" fill="none" viewBox="0 0 24 24" stroke-width="1.8" '
        'stroke="currentColor" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        css, path,
    )


@register.filter
def payment_state_label(state):
    """Etiqueta legible de un estado de cobro: «Impagada», «Parcial», «Pagada».

    Existe porque `get_payment_state_display()` **no**: `payment_state` no es un
    campo del modelo —es una property derivada y una anotación—, así que Django
    no genera su `get_..._display`. Y en una plantilla un método inexistente no
    falla: se resuelve a vacío, y la celda saldría en blanco sin que nadie se
    entere. Por eso la traducción vive aquí y no en el template.
    """
    from billing.models import PatientInvoice

    try:
        return PatientInvoice.PaymentState(state).label
    except ValueError:
        return ''


@register.filter
def invoice_status_badge(status):
    """Clases de fondo/texto del badge de un estado de factura."""
    return INVOICE_STATUS_BADGE_CLASSES.get(status, 'bg-muted-strong text-content-muted')


@register.filter
def invoice_status_icon(status, css='h-4 w-4'):
    """SVG del icono de un estado, con las clases de tamaño que se le pasen.

    El color lo hereda del contenedor (`currentColor`), así que combina con
    `invoice_status_badge`.
    """
    path = INVOICE_STATUS_ICON_PATHS.get(status, INVOICE_STATUS_ICON_PATHS['draft'])
    return format_html(
        '<svg class="{}" fill="none" viewBox="0 0 24 24" stroke-width="1.8" '
        'stroke="currentColor" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        css, path,
    )


@register.simple_tag(takes_context=True)
def invoice_query(context, **overrides):
    """Query string del listado, con los cambios que se le pasen.

    Uso: `{% invoice_query page=3 %}`, `{% invoice_query **sort_date_params %}`.

    Parte de `invoice_query_base` —los filtros YA normalizados por
    `InvoiceFilters`— y no de `request.GET`, para no arrastrar de enlace en
    enlace parámetros inventados o vacíos que venían en la dirección original.
    Es el motivo de que ordenar o pasar de página no pierda nunca los filtros ni
    los duplique: hay una sola representación de ellos y se reconstruye entera.

    Un valor vacío quita el parámetro (así `{% invoice_query page='' %}` vuelve a
    la primera página).
    """
    params = dict(context.get('invoice_query_base') or {})
    for key, value in overrides.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    return urlencode(params)
