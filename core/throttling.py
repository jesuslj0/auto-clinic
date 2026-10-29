"""Contadores de intentos fallidos con bloqueo temporal.

Una pieza pequeña y sin estado propio: todo vive en la caché (Redis, compartida
entre procesos y reinicios — ver `CACHES` en `config/settings/base.py`). La usan
el login, el segundo factor y el endpoint de tokens de la API.

Dos claves por contador:

- `…:fails` cuenta los fallos y caduca sola al cabo de la ventana: quien falla de
  vez en cuando no se acumula para siempre.
- `…:lock` existe mientras dura el bloqueo. Se crea al llegar al límite y se
  consulta ANTES de mirar la credencial: un bloqueo que aun así comprobara la
  contraseña le seguiría diciendo al atacante si acertó.

El identificador (correo, IP, id de usuario) se guarda con hash: la clave no deja
en Redis ni correos ni direcciones legibles.
"""
from __future__ import annotations

import hashlib
import ipaddress

from django.core.cache import cache


def _key(scope: str, ident: str, kind: str) -> str:
    digest = hashlib.sha256(str(ident).strip().lower().encode()).hexdigest()[:40]
    return f'throttle:{scope}:{digest}:{kind}'


def ip_bucket(ip: str | None) -> str:
    """La IP tal cual en IPv4; en IPv6, su /64.

    Una conexión doméstica o de empresa recibe un /64 entero (2^64 direcciones):
    contar por dirección exacta dejaría a quien tenga uno cambiar de IP en cada
    intento.
    """
    if not ip:
        return ''
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        return str(ipaddress.ip_network(f'{ip}/64', strict=False).network_address)
    return str(address)


def is_locked(scope: str, ident: str) -> bool:
    if not ident:
        return False
    return bool(cache.get(_key(scope, ident, 'lock')))


def register_failure(scope: str, ident: str, *, limit: int, window: int, lockout: int) -> bool:
    """Suma un fallo. Devuelve `True` si con este queda bloqueado.

    `incr` es atómico en Redis: dos peticiones simultáneas no se pisan el contador.
    """
    if not ident:
        return False
    fails_key = _key(scope, ident, 'fails')
    cache.add(fails_key, 0, timeout=window)
    try:
        count = cache.incr(fails_key)
    except ValueError:  # expiró entre el add y el incr
        cache.set(fails_key, 1, timeout=window)
        count = 1
    if count >= limit:
        cache.set(_key(scope, ident, 'lock'), 1, timeout=lockout)
        cache.delete(fails_key)
        return True
    return False


def reset(scope: str, ident: str) -> None:
    """Olvida los fallos (no un bloqueo ya activo: ese caduca solo)."""
    if ident:
        cache.delete(_key(scope, ident, 'fails'))


def clear(scope: str, ident: str) -> None:
    """Olvida fallos y bloqueo. Para el desbloqueo manual y los tests."""
    if ident:
        cache.delete_many([_key(scope, ident, 'fails'), _key(scope, ident, 'lock')])
