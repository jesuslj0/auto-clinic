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
