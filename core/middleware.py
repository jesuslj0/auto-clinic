"""Cierra la sesión de quien debería haber pasado por el segundo factor y no lo hizo.

El login normal nunca abre una sesión a medias (`core/two_factor.py`), así que esto
no es la puerta principal, sino la red de seguridad para lo que se cuele por otro
lado:

- **Sesiones anteriores a activar la verificación.** Quien ya estaba dentro con la
  contraseña sola se queda fuera en su siguiente petición y tiene que volver a
  entrar —ahora con el código—. Sin esto, la obligatoriedad solo valdría para los
  logins nuevos.
- **Otras vías de entrada** (`force_login`, el login del admin de Django, un rol
  que se acaba de ascender): sin la marca de «verificada», no hay sesión.

El coste es nulo para casi todas las peticiones: con la marca puesta se sale antes
de tocar la base de datos.
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.two_factor import requires_two_factor, session_is_verified


class TwoFactorEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            user is not None
            and user.is_authenticated
            and not session_is_verified(request)
            and requires_two_factor(user)
        ):
            logout(request)
            if request.path.startswith('/api/'):
                return JsonResponse(
                    {'detail': 'Se requiere verificación en dos pasos. Vuelve a iniciar sesión.'},
                    status=403,
                )
            messages.info(
                request, 'Vuelve a iniciar sesión: ahora se pide un código de verificación.'
            )
            return redirect(f'{reverse("core:login")}?next={request.get_full_path()}')
        return self.get_response(request)
