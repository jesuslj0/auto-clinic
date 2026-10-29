"""Vistas de la verificación en dos pasos.

Tres grupos, todos con sesión salvo el primero:

1. **Login** (`/login/verificacion/…`): el usuario acertó la contraseña pero aún no
   tiene sesión; solo existe el «pendiente» de `core/two_factor.py`. Aquí se pide el
   código, o se configura por primera vez, y solo al terminar se abre la sesión.
2. **«Mi cuenta → Seguridad»**: activar (quien no la exige su rol), regenerar los
   códigos de recuperación, cambiar de móvil y desactivar. Todo lo que debilita o
   sustituye el factor vuelve a pedir contraseña y un código.
3. **Reseteo por un admin** (`TwoFactorResetView`): para quien pierde el móvil y los
   códigos. Borra el factor y las sesiones de esa persona; en su próximo acceso lo
   vuelve a configurar.

Los fallos de código comparten contador con el del login (`core/login_guard.py`).
Ninguna de estas páginas se guarda en caché ni manda `Referer`: enseñan un secreto.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.sessions.models import Session
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from audit.context import acting_as
from core import login_guard, two_factor
from core.forms import SecurityReauthForm, TwoFactorCodeForm
from core.mixins import ClinicAdminRequiredMixin
from core.models import RecoveryCode, TOTPDevice, User


def _protect(response):
    """Cabeceras de una página que enseña un secreto.

    `same-origin` y NO `no-referrer`: con este último el navegador manda
    `Origin: null` en los POST del propio formulario y Django los rechaza por CSRF.
    """
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _safe_next(request, url: str) -> str:
    allowed = url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if allowed else settings.LOGIN_REDIRECT_URL


def _code_from(request) -> str:
    form = TwoFactorCodeForm(request.POST)
    return form.cleaned_data['code'] if form.is_valid() else ''


# ---------------------------------------------------------------------------
# 1. En el login, antes de tener sesión
# ---------------------------------------------------------------------------


@method_decorator([never_cache, sensitive_post_parameters('code')], name='dispatch')
class _PendingLoginView(View):
    """Base: exige un login pendiente (contraseña acertada, segundo factor sin pasar)."""

    def dispatch(self, request, *args, **kwargs):
        pending = two_factor.get_pending(request)
        if pending is None:
            return redirect('core:login')
        self.user, self.pending = pending
        return super().dispatch(request, *args, **kwargs)

    def locked_response(self, request):
        """Bloqueado: el pendiente se descarta y hay que volver a empezar."""
        two_factor.clear_pending(request)
        return _protect(render(
            request, 'registration/two_factor_verify.html', {'locked': True}, status=429
        ))

    def finish(self, request, *, detail):
        two_factor.complete_login(request, self.user, self.pending['backend'], detail=detail)
        return _safe_next(request, self.pending.get('next', ''))


class TwoFactorVerifyView(_PendingLoginView):
    """Pide el código de la app (o uno de recuperación) a quien ya lo tiene."""

    def get(self, request):
        if not two_factor.has_confirmed_device(self.user):
            return redirect('core:two-factor-setup')
        return _protect(render(
            request, 'registration/two_factor_verify.html', {'email': self.user.email}
        ))

    def post(self, request):
        if login_guard.mfa_locked(request, self.user):
            return self.locked_response(request)

        code = _code_from(request)
        factor = two_factor.verify_second_factor(self.user, code) if code else None
        if factor is None:
            if login_guard.record_mfa_failure(request, self.user):
                return self.locked_response(request)
            return _protect(render(request, 'registration/two_factor_verify.html', {
                'email': self.user.email,
                'error': 'El código no es correcto. Comprueba que la hora de tu móvil es la correcta.',
            }))

        login_guard.record_mfa_success(self.user)
        recovery = factor.kind == 'recovery'
        if recovery:
            two_factor.log_change(
                request, self.user, 'Entró con un código de recuperación', actor=self.user
            )
        target = self.finish(
            request,
            detail='Inicio de sesión con código de recuperación' if recovery
            else 'Inicio de sesión con verificación en dos pasos',
        )
        if recovery:
            left = two_factor.remaining_recovery_codes(self.user)
            messages.warning(
                request,
                f'Has entrado con un código de recuperación. Te quedan {left}. '
                'Genera unos nuevos en Mi cuenta → Seguridad.',
            )
        return redirect(target)


class TwoFactorLoginSetupView(_PendingLoginView):
    """Primera configuración, antes de entrar: QR, primer código y códigos de recuperación."""

    def get(self, request):
        if two_factor.has_confirmed_device(self.user):
            return redirect('core:two-factor-verify')
        return _protect(render(request, 'registration/two_factor_setup.html', _setup_context(self.user)))

    def post(self, request):
        if two_factor.has_confirmed_device(self.user):
            return redirect('core:two-factor-verify')
        if login_guard.mfa_locked(request, self.user):
            return self.locked_response(request)

        device = two_factor.start_enrollment(self.user)
        if not two_factor.verify_totp(device, _code_from(request)):
            if login_guard.record_mfa_failure(request, self.user):
                return self.locked_response(request)
            return _protect(render(request, 'registration/two_factor_setup.html', {
                **_setup_context(self.user),
                'error': 'El código no es correcto. Escanea el QR de nuevo y prueba con el que muestra la app ahora.',
            }))

        login_guard.record_mfa_success(self.user)
        two_factor.confirm_device(device)
        codes = two_factor.generate_recovery_codes(self.user)
        two_factor.log_change(
            request, self.user, 'Activó la verificación en dos pasos', actor=self.user
        )
        target = self.finish(request, detail='Inicio de sesión tras activar la verificación en dos pasos')
        return _protect(render(request, 'registration/two_factor_codes.html', {
            'codes': codes, 'continue_url': target, 'first_time': True,
        }))


def _setup_context(user):
    device = two_factor.start_enrollment(user)
    return {
        'email': user.email,
        'qr_svg': two_factor.qr_svg(two_factor.provisioning_uri(device)),
        'manual_key': two_factor.manual_key(device),
    }


# ---------------------------------------------------------------------------
# 2. «Mi cuenta → Seguridad»
# ---------------------------------------------------------------------------


@method_decorator([never_cache, sensitive_post_parameters('password', 'code')], name='dispatch')
class _SecurityActionView(LoginRequiredMixin, View):
    """Base de las acciones de la pestaña Seguridad."""

    security_url = 'core:account-security'

    def back(self):
        return redirect(self.security_url)

    def reauthenticate(self, request) -> bool:
        """Contraseña + un código vigente (de la app o de recuperación).

        Es lo que se exige para tocar el factor: una sesión abierta en un móvil
        prestado no basta para quitárselo a su dueño. Los fallos cuentan para el
        mismo bloqueo que el login.
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
                messages.error(request, 'La contraseña o el código no son correctos.')
            return False
        login_guard.record_mfa_success(user)
        return True


class TwoFactorAccountSetupView(LoginRequiredMixin, View):
    """Activa la verificación estando dentro (quien no la exige su rol, o cambia de móvil)."""

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        if two_factor.has_confirmed_device(request.user):
            messages.info(request, 'La verificación en dos pasos ya está activa.')
            return redirect('core:account-security')
        return _protect(render(request, 'registration/two_factor_setup.html', {
            **_setup_context(request.user), 'account_mode': True,
        }))

    @method_decorator(sensitive_post_parameters('code'))
    def post(self, request):
        user = request.user
        if two_factor.has_confirmed_device(user):
            return redirect('core:account-security')
        if login_guard.mfa_locked(request, user):
            messages.error(request, login_guard.LOCKED_MESSAGE)
            return redirect('core:account-security')

        device = two_factor.start_enrollment(user)
        if not two_factor.verify_totp(device, _code_from(request)):
            if login_guard.record_mfa_failure(request, user):
                messages.error(request, login_guard.LOCKED_MESSAGE)
                return redirect('core:account-security')
            return _protect(render(request, 'registration/two_factor_setup.html', {
                **_setup_context(user),
                'account_mode': True,
                'error': 'El código no es correcto. Escanea el QR de nuevo y prueba con el que muestra la app ahora.',
            }))

        login_guard.record_mfa_success(user)
        two_factor.confirm_device(device)
        codes = two_factor.generate_recovery_codes(user)
        two_factor.log_change(request, user, 'Activó la verificación en dos pasos')
        # Acaba de demostrar el factor: sin esta marca, el middleware le cerraría
        # la sesión en la siguiente petición.
        request.session[two_factor.VERIFIED_KEY] = True
        return _protect(render(request, 'registration/two_factor_codes.html', {
            'codes': codes,
            'continue_url': reverse('core:account-security'),
            'account_mode': True,
            'first_time': True,
        }))


class RecoveryCodesRegenerateView(_SecurityActionView):
    def post(self, request):
        if not two_factor.has_confirmed_device(request.user):
            return self.back()
        if not self.reauthenticate(request):
            return self.back()
        codes = two_factor.generate_recovery_codes(request.user)
        two_factor.log_change(request, request.user, 'Regeneró los códigos de recuperación')
        return _protect(render(request, 'registration/two_factor_codes.html', {
            'codes': codes,
            'continue_url': reverse('core:account-security'),
            'account_mode': True,
        }))


class TwoFactorChangeDeviceView(_SecurityActionView):
    """Cambio de móvil: sustituye el factor por uno nuevo, que se confirma a continuación."""

    def post(self, request):
        if not two_factor.has_confirmed_device(request.user):
            return self.back()
        if not self.reauthenticate(request):
            return self.back()
        two_factor.restart_enrollment(request.user)
        two_factor.log_change(request, request.user, 'Inició el cambio de dispositivo')
        return redirect('core:two-factor-account-setup')


class TwoFactorDisableView(_SecurityActionView):
    def post(self, request):
        user = request.user
        if two_factor.required_by_role(user):
            messages.error(request, 'Tu rol exige la verificación en dos pasos: no se puede desactivar.')
            return self.back()
        if not two_factor.has_confirmed_device(user):
            return self.back()
        if not self.reauthenticate(request):
            return self.back()
        _remove_factor(user)
        two_factor.log_change(request, user, 'Desactivó la verificación en dos pasos')
        messages.success(request, 'Verificación en dos pasos desactivada.')
        return self.back()


def _remove_factor(user) -> None:
    """Quita dispositivo y códigos, uno a uno para que cada baja quede auditada."""
    with acting_as(user):
        for device in TOTPDevice.objects.filter(user=user):
            device.delete()
        for code in RecoveryCode.objects.filter(user=user):
            code.delete()


# ---------------------------------------------------------------------------
# 3. Reseteo por un administrador
# ---------------------------------------------------------------------------


def invalidate_sessions(user) -> int:
    """Cierra todas las sesiones abiertas de `user`. Devuelve cuántas.

    Django no indexa las sesiones por usuario, así que se recorren las vigentes. Son
    pocas (personal de una clínica); si algún día no lo fueran, habría que guardar
    el usuario en la sesión.
    """
    closed = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get('_auth_user_id') == str(user.pk):
            session.delete()
            closed += 1
    return closed


@method_decorator(never_cache, name='dispatch')
class TwoFactorResetView(ClinicAdminRequiredMixin, View):
    """Un admin quita la verificación en dos pasos a otro usuario de su clínica.

    Es la salida para quien pierde el móvil y los códigos de recuperación. También
    sirve si alguien se adelantó a configurar el factor de una cuenta ajena (el
    primer alta se hace con solo la contraseña): por eso cierra además las sesiones
    de esa persona. Un administrador no puede resetearse a sí mismo: para eso están
    «Cambiar de dispositivo» y los códigos de recuperación.
    """

    def post(self, request, user_id):
        target = get_object_or_404(User, pk=user_id)
        if not request.user.is_superuser and target.clinic_id != request.user.clinic_id:
            # Otra clínica: no existe, a efectos de este usuario.
            raise Http404
        back = _safe_next(request, request.POST.get('next', ''))
        if target.pk == request.user.pk:
            messages.error(request, 'No puedes resetear tu propia verificación desde aquí.')
            return redirect(back)

        had_factor = two_factor.has_confirmed_device(target) or TOTPDevice.objects.filter(user=target).exists()
        _remove_factor(target)
        closed = invalidate_sessions(target)
        two_factor.log_change(
            request, target,
            f'Reseteada por {request.user.email} ({closed} sesión(es) cerrada(s))',
        )
        if had_factor:
            messages.success(
                request,
                f'Verificación en dos pasos de {target.email} restablecida. '
                'Tendrá que configurarla de nuevo la próxima vez que entre.',
            )
        else:
            messages.info(request, f'{target.email} no tenía verificación en dos pasos configurada.')
        return redirect(back)
