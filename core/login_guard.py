"""Límite de intentos del login y del segundo factor.

Se cuenta por dos lados a la vez, y por eso hacen falta los dos:

- **Por correo**: frena a quien prueba contraseñas contra una cuenta aunque cambie
  de IP (una botnet). Cuenta el correo tecleado, exista o no, para que el bloqueo
  no delate qué cuentas existen.
- **Por IP**: frena a quien prueba muchos correos desde un mismo sitio. Es más
  generosa (varias personas pueden compartir salida a Internet).

Un bloqueo por correo es temporal y se levanta solo: si fuera permanente, cualquiera
podría dejar fuera a un usuario sin saber su contraseña.

Todo lo que se decide aquí se anota en el registro de accesos (`AccessLog`): los
intentos fallidos y los bloqueos no cambian ningún modelo, así que no dejarían
rastro por las señales. Un bloqueo se anota UNA vez, al producirse; los intentos
que rebotan contra él no escriben nada, para que un ataque no llene la tabla.
"""
from __future__ import annotations

from django.conf import settings

from audit.context import client_ip
from audit.mixins import log_access
from audit.models import AccessLog
from core import throttling

LOGIN_EMAIL = 'login-email'
LOGIN_IP = 'login-ip'
MFA_USER = 'mfa-user'
MFA_IP = 'mfa-ip'

LOCKED_MESSAGE = 'Demasiados intentos. Espera unos minutos antes de volver a intentarlo.'


def normalize_email(raw) -> str:
    return (raw or '').strip().lower()[:254]


def _ip(request) -> str:
    return throttling.ip_bucket(client_ip(request))


# --- Contraseña ----------------------------------------------------------------


def login_locked(request, email: str) -> bool:
    return throttling.is_locked(LOGIN_EMAIL, email) or throttling.is_locked(LOGIN_IP, _ip(request))


def record_login_failure(request, email: str) -> None:
    window = settings.LOGIN_FAILURE_WINDOW_SECONDS
    lockout = settings.LOGIN_LOCKOUT_SECONDS
    locked_email = throttling.register_failure(
        LOGIN_EMAIL, email,
        limit=settings.LOGIN_MAX_FAILURES_PER_EMAIL, window=window, lockout=lockout,
    )
    locked_ip = throttling.register_failure(
        LOGIN_IP, _ip(request),
        limit=settings.LOGIN_MAX_FAILURES_PER_IP, window=window, lockout=lockout,
    )
    log_access(
        action=AccessLog.Action.LOGIN_FAILED,
        description=f'Contraseña incorrecta para {email}',
        request=request,
    )
    if locked_email or locked_ip:
        what = ' y '.join(
            name for name, hit in (('el correo', locked_email), ('la IP', locked_ip)) if hit
        )
        log_access(
            action=AccessLog.Action.LOGIN_LOCKED,
            description=f'Bloqueado {what} ({email}) durante {lockout // 60} min',
            request=request,
        )


def record_login_success(email: str) -> None:
    """Solo se olvidan los fallos del correo: los de la IP no son de esta persona."""
    throttling.reset(LOGIN_EMAIL, email)


# --- Segundo factor ------------------------------------------------------------


def mfa_locked(request, user) -> bool:
    return throttling.is_locked(MFA_USER, user.pk) or throttling.is_locked(MFA_IP, _ip(request))


def record_mfa_failure(request, user) -> bool:
    """Suma un código incorrecto. Devuelve `True` si deja bloqueado al usuario."""
    window = settings.LOGIN_FAILURE_WINDOW_SECONDS
    lockout = settings.TWO_FACTOR_LOCKOUT_SECONDS
    locked_user = throttling.register_failure(
        MFA_USER, user.pk,
        limit=settings.TWO_FACTOR_MAX_FAILURES, window=window, lockout=lockout,
    )
    locked_ip = throttling.register_failure(
        MFA_IP, _ip(request),
        limit=settings.LOGIN_MAX_FAILURES_PER_IP, window=window, lockout=lockout,
    )
    log_access(
        action=AccessLog.Action.LOGIN_FAILED,
        description='Código de verificación incorrecto',
        request=request,
        actor=user,
    )
    if locked_user or locked_ip:
        log_access(
            action=AccessLog.Action.LOGIN_LOCKED,
            description=f'Verificación en dos pasos bloqueada durante {lockout // 60} min',
            request=request,
            actor=user,
        )
    return locked_user or locked_ip


def record_mfa_success(user) -> None:
    throttling.reset(MFA_USER, user.pk)
