from django.utils.functional import SimpleLazyObject


def notification_unread(request):
    """Avisos sin leer del usuario para la campana de la barra superior (perezoso)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.clinic_id:
        return {}

    from notifications.inbox import unread_count

    return {'notification_unread_total': SimpleLazyObject(lambda: unread_count(user))}
