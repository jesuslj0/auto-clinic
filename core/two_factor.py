"""Verificación en dos pasos con TOTP (RFC 6238).

Es el estándar abierto que entiende cualquier app de autenticación (Google
Authenticator, Microsoft Authenticator, Authy, Aegis, 2FAS, Bitwarden, 1Password…):
el QR solo lleva una URI `otpauth://` con el secreto, y el servidor no habla con
nadie. Se usan los parámetros por defecto —SHA-1, 6 dígitos, 30 s— porque Google
Authenticator ignora los demás y generaría códigos que aquí no valdrían.

Reglas, por si se toca:

- **Quién la necesita.** Admins (`is_clinic_admin`) y profesionales (quien tiene
  `professional_profile`), mientras `TWO_FACTOR_REQUIRED_FOR_ROLES`. Quien ya tiene
  un dispositivo confirmado la usa siempre, lo exija o no su rol.
- **Nunca hay una sesión a medias.** Tras acertar la contraseña NO se llama a
  `login()`: el usuario queda «pendiente» en la sesión (`start_pending`) y solo
  entra cuando pasa el segundo factor. Esto incluye la primera configuración, que
  también se hace antes de entrar.
- **El secreto va cifrado**, con Fernet (`TWO_FACTOR_ENCRYPTION_KEY`), y un código
  solo vale una vez (`last_used_step`).
- **Los códigos de recuperación** se guardan como HMAC y se muestran una sola vez.
- **Todo cambio deja rastro** en `AccessLog` (`TWO_FACTOR_CHANGE`) y los modelos
  están en la auditoría (`core/apps.py`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.utils import timezone

from audit.context import acting_as
from audit.mixins import log_access
from audit.models import AccessLog
from core.models import RecoveryCode, TOTPDevice

STEP_SECONDS = 30
DIGITS = 6
#: Intervalos aceptados a cada lado del actual: tolera un móvil con el reloj
#: desajustado hasta 30 s.
DRIFT_STEPS = 1

RECOVERY_CODE_COUNT = 10
#: Sin 0/O, 1/I/L ni U/V: se teclean desde un papel.
RECOVERY_ALPHABET = 'ABCDEFGHJKMNPQRSTWXYZ23456789'

PENDING_KEY = 'two_factor_pending'
VERIFIED_KEY = 'two_factor_verified'


# --- Quién la necesita ----------------------------------------------------------


def has_confirmed_device(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def required_by_role(user) -> bool:
    """¿Se la exige su rol? Independiente de si ya la tiene configurada."""
    from core.mixins import is_clinic_admin

    if not settings.TWO_FACTOR_REQUIRED_FOR_ROLES:
        return False
    return is_clinic_admin(user) or getattr(user, 'professional_profile', None) is not None


def requires_two_factor(user) -> bool:
    """¿Debe este usuario pasar por el segundo factor para tener sesión?"""
    return required_by_role(user) or has_confirmed_device(user)


# --- Secreto cifrado ------------------------------------------------------------


def _fernet() -> MultiFernet:
    raw = [key.strip() for key in settings.TWO_FACTOR_ENCRYPTION_KEY.split(',') if key.strip()]
    if not raw:
        # Sin clave propia, una derivada de `SECRET_KEY`: cifra igual, pero rotar la
        # `SECRET_KEY` dejaría los secretos ilegibles. Por eso en producción se define.
        digest = hashlib.sha256(f'two-factor|{settings.SECRET_KEY}'.encode()).digest()
        raw = [base64.urlsafe_b64encode(digest).decode()]
    return MultiFernet([Fernet(key) for key in raw])


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # clave rotada sin conservar la anterior
        raise ValueError('No se puede descifrar el secreto de verificación en dos pasos.') from exc


# --- Dispositivo ----------------------------------------------------------------


def start_enrollment(user) -> TOTPDevice:
    """El dispositivo sin confirmar de este usuario, creándolo si no existe.

    Se reutiliza el pendiente para que recargar la página no cambie el QR (el
    usuario podría haberlo escaneado ya). Con uno confirmado no hace nada: hay que
    quitarlo antes.
    """
    with acting_as(user):
        device = TOTPDevice.objects.filter(user=user).first()
        if device is None:
            device = TOTPDevice(user=user, secret=encrypt_secret(pyotp.random_base32()))
            device.save()
        return device


def restart_enrollment(user) -> TOTPDevice:
    """Sustituye el dispositivo (confirmado o no) por uno nuevo sin confirmar."""
    with acting_as(user):
        existing = TOTPDevice.objects.filter(user=user).first()
        if existing is not None:
            existing.delete()
        _delete_recovery_codes(user)
        device = TOTPDevice(user=user, secret=encrypt_secret(pyotp.random_base32()))
        device.save()
        return device


def provisioning_uri(device: TOTPDevice) -> str:
    return pyotp.TOTP(
        decrypt_secret(device.secret), digits=DIGITS, interval=STEP_SECONDS
    ).provisioning_uri(name=device.user.email, issuer_name=settings.TWO_FACTOR_ISSUER)


def manual_key(device: TOTPDevice) -> str:
    """El secreto en grupos de cuatro, para teclearlo si no se puede escanear."""
    secret = decrypt_secret(device.secret)
    return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))


def qr_svg(uri: str) -> str:
    """El QR como SVG en línea, generado aquí: el secreto no sale a ningún tercero."""
    code = segno.make(uri, error='m', micro=False)
    return code.svg_inline(border=2, dark='#000000', light='#ffffff', omitsize=True)


def verify_totp(device: TOTPDevice, raw: str) -> bool:
    """¿Es `raw` el código vigente? Lo consume: no vale una segunda vez."""
    code = ''.join(ch for ch in (raw or '') if not ch.isspace())
    if len(code) != DIGITS or not code.isdigit():
        return False
    with transaction.atomic():
        # Bajo bloqueo de fila: dos envíos simultáneos del mismo código no pueden
        # ganar los dos.
        locked = TOTPDevice.objects.select_for_update().get(pk=device.pk)
        totp = pyotp.TOTP(decrypt_secret(locked.secret), digits=DIGITS, interval=STEP_SECONDS)
        current = int(time.time()) // STEP_SECONDS
        accepted = None
        for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
            if hmac.compare_digest(totp.at(step * STEP_SECONDS), code) and step > locked.last_used_step:
                accepted = step
        if accepted is None:
            return False
        locked.last_used_step = accepted
        locked.save(update_fields=['last_used_step', 'updated_at'])
    device.last_used_step = accepted
    return True


def confirm_device(device: TOTPDevice) -> None:
    with acting_as(device.user):
        device.confirmed_at = timezone.now()
        device.save(update_fields=['confirmed_at', 'updated_at'])


# --- Códigos de recuperación ----------------------------------------------------


def _normalize_recovery(raw: str) -> str:
    return ''.join(ch for ch in (raw or '').upper() if ch.isalnum())


def _hash_recovery(normalized: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f'recovery|{normalized}'.encode(), hashlib.sha256
    ).hexdigest()


def looks_like_recovery_code(raw: str) -> bool:
    return len(_normalize_recovery(raw)) == 10


def _delete_recovery_codes(user) -> None:
    # Uno a uno: un `queryset.delete()` masivo saltaría la auditoría.
    for code in RecoveryCode.objects.filter(user=user):
        code.delete()


def generate_recovery_codes(user) -> list[str]:
    """Sustituye los códigos del usuario por unos nuevos y los devuelve EN CLARO.

    Es la única vez que existen en claro: en la base solo queda su HMAC.
    """
    with acting_as(user), transaction.atomic():
        _delete_recovery_codes(user)
        codes = []
        for _ in range(RECOVERY_CODE_COUNT):
            raw = ''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
            RecoveryCode(user=user, code_hash=_hash_recovery(raw)).save()
            codes.append(f'{raw[:5]}-{raw[5:]}')
        return codes


def consume_recovery_code(user, raw: str) -> bool:
    normalized = _normalize_recovery(raw)
    if len(normalized) != 10:
        return False
    hashed = _hash_recovery(normalized)
    with transaction.atomic():
        row = (
            RecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=hashed, used_at__isnull=True)
            .first()
        )
        if row is None:
            return False
        with acting_as(user):
            row.used_at = timezone.now()
            row.save(update_fields=['used_at', 'updated_at'])
    return True


def remaining_recovery_codes(user) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


# --- Verificar un segundo factor ------------------------------------------------


@dataclass(frozen=True)
class SecondFactor:
    kind: str  # 'totp' | 'recovery'


def verify_second_factor(user, raw: str) -> SecondFactor | None:
    """Acepta un código de la app o uno de recuperación. `None` si no vale ninguno."""
    device = TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        return None
    if looks_like_recovery_code(raw):
        return SecondFactor('recovery') if consume_recovery_code(user, raw) else None
    return SecondFactor('totp') if verify_totp(device, raw) else None


# --- El login a medias ----------------------------------------------------------


def start_pending(request, user, next_url: str = '') -> None:
    """Deja anotado en la sesión quién ha acertado la contraseña (y solo eso)."""
    request.session[PENDING_KEY] = {
        'user_id': user.pk,
        'backend': getattr(user, 'backend', 'django.contrib.auth.backends.ModelBackend'),
        'next': next_url,
        'expires': time.time() + settings.TWO_FACTOR_PENDING_SECONDS,
    }


def get_pending(request):
    """`(usuario, datos)` del login pendiente, o `None` si no hay o caducó."""
    from core.models import User

    data = request.session.get(PENDING_KEY)
    if not data:
        return None
    if data.get('expires', 0) < time.time():
        clear_pending(request)
        return None
    user = User.objects.filter(pk=data['user_id'], is_active=True).first()
    if user is None:
        clear_pending(request)
        return None
    return user, data


def clear_pending(request) -> None:
    request.session.pop(PENDING_KEY, None)


def complete_login(request, user, backend: str, *, detail: str) -> None:
    """Abre la sesión de verdad y la marca como verificada."""
    clear_pending(request)
    login(request, user, backend=backend)
    request.session[VERIFIED_KEY] = True
    log_access(
        action=AccessLog.Action.LOGIN,
        description=detail,
        request=request,
    )


def log_change(request, subject, detail: str, *, actor=None) -> None:
    """Rastro de un cambio en la verificación en dos pasos de `subject`.

    `actor` solo hace falta en el login a medias, cuando la petición aún es anónima;
    con sesión, el actor es quien la tiene (el propio usuario o el admin que resetea).
    """
    log_access(
        action=AccessLog.Action.TWO_FACTOR_CHANGE,
        obj=subject,
        description=detail,
        request=request,
        actor=actor,
    )


def session_is_verified(request) -> bool:
    return bool(request.session.get(VERIFIED_KEY))
