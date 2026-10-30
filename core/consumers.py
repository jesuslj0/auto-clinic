from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from core.realtime import clinic_group_name

# Códigos de cierre propios (rango 4000-4999, reservado a la aplicación). El
# cliente los usa para distinguir "no reintentes" de un corte de red.
CLOSE_UNAUTHENTICATED = 4401
CLOSE_NO_CLINIC = 4403


@database_sync_to_async
def _missing_second_factor(user, session) -> bool:
    """¿Debería esta sesión haber pasado por el segundo factor y no lo hizo?

    El middleware HTTP cierra esas sesiones, pero un socket abierto con una cookie
    anterior a activar la verificación no pasa por él.
    """
    # Import perezoso a propósito: `config/asgi.py` importa los routings (y con ellos
    # este módulo) ANTES de `get_asgi_application()`, es decir, antes de que las apps
    # estén cargadas, y `core.two_factor` arrastra los modelos. A nivel de módulo,
    # `daphne` muere al arrancar con `AppRegistryNotReady`.
    from core.two_factor import VERIFIED_KEY, requires_two_factor

    verified = bool(session and session.get(VERIFIED_KEY))
    return not verified and requires_two_factor(user)


class ClinicScopedConsumer(AsyncWebsocketConsumer):
    """Consumer de solo lectura suscrito a los eventos de la clínica del usuario.

    La clínica sale siempre de `scope['user']`, nunca de la URL: así el
    aislamiento entre clínicas no depende de lo que mande el cliente.

    No acepta mensajes del cliente. Escribir sigue siendo un POST HTTP normal,
    con su autenticación, CSRF y validación, y un único camino de escritura.
    """

    # Flujo de eventos al que se suscribe (define el nombre del grupo).
    stream = None

    async def connect(self):
        self.group_name = None
        user = self.scope.get('user')

        # Se acepta antes de cerrar para que el código llegue al navegador: un
        # rechazo durante el handshake se ve como 1006, indistinguible de un
        # corte de red, y el cliente reintentaría para siempre.
        if user is None or not user.is_authenticated or not user.is_active:
            await self.accept()
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        if await _missing_second_factor(user, self.scope.get('session')):
            await self.accept()
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        # El equipo de plataforma no tiene clínica y, por tanto, nada que escuchar.
        if not user.clinic_id:
            await self.accept()
            await self.close(code=CLOSE_NO_CLINIC)
            return

        self.group_name = clinic_group_name(self.stream, user.clinic_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Solo lectura: lo que mande el cliente se ignora.
        pass
