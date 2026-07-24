"""Contexto de petición para la auditoría.

Las señales de Django no reciben el `request`, pero un registro de auditoría sin
usuario, IP ni user agent no sirve para nada. Este módulo mantiene esos datos en
un `contextvars.ContextVar`.

**Por qué `contextvars` y no `threading.local`:** el proyecto se sirve con Daphne
(ASGI) y tiene consumidores de Channels. Bajo ASGI varias peticiones comparten
hilo, así que un `threading.local` filtraría el usuario de una petición a otra
—exactamente el fallo que un log de accesos a datos de salud no se puede
permitir—. Los `contextvars` son propios de cada tarea asíncrona y se copian al
entrar en un `sync_to_async`, así que funcionan en los dos mundos.

**Resolución perezosa del actor:** el contexto guarda el `request`, no el
usuario. El motivo es DRF: cuando el middleware corre, la autenticación de DRF
(`Api-Key` de n8n, `TokenAuthentication`) todavía no ha ocurrido, así que
`request.user` es anónimo. DRF autentica ya dentro de la vista y propaga el
resultado al `HttpRequest` subyacente, de modo que si resolvemos el actor en el
momento de ESCRIBIR el log —que es dentro de la vista— sí vemos al usuario real
o al `ClinicAgent` de n8n.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

# Orígenes posibles de un evento auditado. Se declaran aquí, y no en `models`,
# para que el middleware pueda importarlos sin arrastrar el ORM.
ORIGIN_WEB = 'web'
ORIGIN_API = 'api'
ORIGIN_ADMIN = 'admin'
ORIGIN_COMMAND = 'command'
ORIGIN_N8N = 'n8n'


@dataclass(frozen=True)
class AuditContext:
    """Datos de la petición en curso. Inmutable: se reemplaza, no se muta."""

    request: Any = None
    ip: str | None = None
    user_agent: str = ''
    path: str = ''
    method: str = ''
    origin: str = ORIGIN_COMMAND
    # Actor fijado a mano (comandos de gestión, tareas Celery, tests). Si viene
    # informado gana sobre lo que se pueda deducir del `request`.
    user: Any = None
    user_repr: str = ''


EMPTY_CONTEXT = AuditContext()

_audit_context: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    'audit_context', default=EMPTY_CONTEXT
)


def get_audit_context() -> AuditContext:
    return _audit_context.get()


def set_audit_context(context: AuditContext):
    """Fija el contexto y devuelve el token necesario para restaurarlo."""
    return _audit_context.set(context)


def reset_audit_context(token) -> None:
    """Restaura el contexto anterior. Siempre en un `finally`."""
    _audit_context.reset(token)


def clear_audit_context() -> None:
    _audit_context.set(EMPTY_CONTEXT)


@contextmanager
def audit_context(**kwargs):
    """Fija un contexto temporal.

    Pensado para comandos de gestión, tareas de Celery y tests:

        with audit_context(user=user, origin=ORIGIN_COMMAND):
            paciente.save()
    """
    token = set_audit_context(AuditContext(**kwargs))
    try:
        yield
    finally:
        reset_audit_context(token)


# ---------------------------------------------------------------------------
# Resolución del actor
# ---------------------------------------------------------------------------

def _client_ip(request) -> str | None:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        # El primero de la lista es el cliente original; el resto son proxies.
        return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


def build_context(request) -> AuditContext:
    """Construye el contexto a partir del `HttpRequest`."""
    return AuditContext(
        request=request,
        ip=_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        path=request.path[:500],
        method=request.method or '',
        origin=_guess_origin(request),
    )


def _guess_origin(request) -> str:
    """Origen deducible sin autenticar: cabecera y ruta.

    Es una primera aproximación. `resolve_actor()` la afina cuando ya sabe quién
    es el usuario (por ejemplo, un `ClinicAgent` siempre es `n8n` aunque entre
    por una ruta que no empiece por `/api/`).
    """
    auth = request.META.get('HTTP_AUTHORIZATION', '').strip()
    if auth.split()[:1] == ['Api-Key']:
        return ORIGIN_N8N
    path = request.path or ''
    if path.startswith('/admin/'):
        return ORIGIN_ADMIN
    if path.startswith('/api/'):
        return ORIGIN_API
    return ORIGIN_WEB


def resolve_actor(context: AuditContext | None = None) -> tuple[Any, str, str]:
    """Devuelve `(usuario, usuario_repr, origen)` para el contexto dado.

    El usuario devuelto es o bien una instancia del modelo de usuario del
    proyecto (apta para el FK) o `None`. n8n se autentica con un `ClinicAgent`,
    que no es una fila de la tabla de usuarios: en ese caso el FK queda a nulo y
    la identidad vive solo en `user_repr`.
    """
    from django.contrib.auth import get_user_model

    context = context or get_audit_context()
    user_model = get_user_model()

    # Actor fijado a mano: manda sobre cualquier deducción.
    if context.user is not None:
        if isinstance(context.user, user_model):
            return context.user, context.user_repr or describe_user(context.user), context.origin
        return None, context.user_repr or str(context.user), context.origin

    if context.user_repr:
        return None, context.user_repr, context.origin

    request = context.request
    user = getattr(request, 'user', None) if request is not None else None
    if user is None or not getattr(user, 'is_authenticated', False):
        return None, '', context.origin

    if isinstance(user, user_model):
        return user, describe_user(user), context.origin

    # `ClinicAgent` (n8n) o cualquier otro actor que no sea una fila de usuarios.
    clinic_id = getattr(user, 'clinic_id', None)
    return None, f'n8n:{clinic_id}' if clinic_id else str(user), ORIGIN_N8N


def describe_user(user) -> str:
    """Representación legible y estable del usuario en el momento del evento.

    Se guarda denormalizada para que el log siga siendo interpretable si el
    usuario se da de baja y el FK queda a nulo.
    """
    full_name = (user.get_full_name() or '').strip()
    email = getattr(user, 'email', '') or getattr(user, 'username', '')
    if full_name and email:
        return f'{full_name} <{email}>'[:255]
    return (full_name or email or str(user))[:255]
