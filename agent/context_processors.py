from django.utils.functional import SimpleLazyObject


def chat_unread(request):
    """Total de chats sin leer para el contador del menú lateral.

    Perezoso: solo cuesta una consulta si la plantilla lo pinta, y no en las
    respuestas (fragmentos, API) que no llevan menú.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.clinic_id:
        return {}

    from agent.realtime import clinic_unread_total

    return {'chat_unread_total': SimpleLazyObject(lambda: clinic_unread_total(user.clinic_id))}
