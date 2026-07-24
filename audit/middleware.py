"""Middleware de contexto de auditoría.

Ligero a propósito: solo puebla y limpia el contexto. **No registra accesos.**
Instrumentar las lecturas por middleware generaría ruido masivo sobre estáticos,
health checks y vistas administrativas, y un log en el que todo está registrado
es un log en el que no se encuentra nada. Los accesos se instrumentan vista a
vista con `audit.mixins`.

Debe ir DESPUÉS de `AuthenticationMiddleware` para que `request.user` exista.
"""
from asgiref.sync import iscoroutinefunction
from django.utils.decorators import sync_and_async_middleware

from audit.context import build_context, reset_audit_context, set_audit_context


@sync_and_async_middleware
def AuditContextMiddleware(get_response):
    """Fija el contexto al entrar y lo restaura al salir, pase lo que pase.

    La restauración va en un `finally` y usa el token del `ContextVar`: si una
    vista lanza, el contexto no se queda pegado para la siguiente petición que
    reutilice ese hilo o esa tarea.
    """

    if iscoroutinefunction(get_response):

        async def middleware(request):
            token = set_audit_context(build_context(request))
            try:
                return await get_response(request)
            finally:
                reset_audit_context(token)

    else:

        def middleware(request):
            token = set_audit_context(build_context(request))
            try:
                return get_response(request)
            finally:
                reset_audit_context(token)

    return middleware
