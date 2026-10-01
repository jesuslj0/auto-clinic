from django import template

register = template.Library()


@register.filter
def duration_label(seconds):
    """Segundos en lenguaje de persona: «5 minutos», «1 minuto», «1,5 minutos».

    Por debajo del minuto se queda en segundos; un valor no numérico sale tal cual.
    """
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return seconds
    if seconds < 60:
        return f'{seconds} segundo' + ('' if seconds == 1 else 's')
    minutes = seconds / 60
    if minutes == int(minutes):
        minutes = int(minutes)
        text = str(minutes)
    else:
        text = f'{minutes:.1f}'.replace('.', ',')
    return f'{text} minuto' + ('' if minutes == 1 else 's')
