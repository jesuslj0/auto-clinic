from django import template

register = template.Library()


@register.filter
def minutes_human(minutes):
    """Minutos en una frase corta: 90 → «1 h 30 min», 1440 → «1 día», 45 → «45 min»."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ''
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} día{'s' if days != 1 else ''}")
    if hours:
        parts.append(f'{hours} h')
    if mins or not parts:
        parts.append(f'{mins} min')
    return ' '.join(parts)
