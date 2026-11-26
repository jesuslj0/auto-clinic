"""Volver a pedir contraseña + código antes de una acción delicada.

Es lo que se exige para tocar el segundo factor (`core/two_factor_views.py`) y para
exportar los datos de toda la clínica (`exports/`): una sesión que se quedó abierta
en un móvil o un ordenador prestado no basta.

Los fallos cuentan para el mismo bloqueo que el login (`core/login_guard.py`): es
otra puerta a la misma contraseña, así que no puede dar intentos de más.
"""
from django.contrib import messages

from core import login_guard, two_factor
from core.forms import SecurityReauthForm

WRONG_MESSAGE = 'La contraseña o el código no son correctos.'


def reauthenticate(request) -> bool:
    """Contraseña + un código vigente (de la app o de recuperación), de `request.POST`.

    Devuelve `True` si son correctos. Si no, deja un mensaje de error y devuelve
    `False`. Con el bloqueo activo ni siquiera mira la contraseña.

    Un usuario sin segundo factor confirmado nunca pasa: `verify_second_factor`
    devuelve `None` para él. Quien exija esto de verdad (exportar la clínica) debe
    comprobar `two_factor.has_confirmed_device` antes y explicarlo.
    """
    user = request.user
    if login_guard.mfa_locked(request, user):
        messages.error(request, login_guard.LOCKED_MESSAGE)
        return False
    form = SecurityReauthForm(request.POST)
    valid = (
        form.is_valid()
        and user.check_password(form.cleaned_data['password'])
        and two_factor.verify_second_factor(user, form.cleaned_data['code']) is not None
    )
    if not valid:
        if login_guard.record_mfa_failure(request, user):
            messages.error(request, login_guard.LOCKED_MESSAGE)
        else:
            messages.error(request, WRONG_MESSAGE)
        return False
    login_guard.record_mfa_success(user)
    return True
