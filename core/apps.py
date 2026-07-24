from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from audit import registry

        from core.models import Clinic, User

        # Altas, bajas y cambios de rol de usuarios. `password` y `last_login`
        # los excluye ya el registry por defecto.
        registry.register(User)

        # De la clínica interesan los cambios de configuración, pero sus
        # credenciales no pueden acabar escritas en el log.
        registry.register(
            Clinic,
            sensitive=[
                'whatsapp_token',
                'whatsapp_verify_token',
                'agent_api_key',
                'api_key',
                'calendly_token',
            ],
        )
