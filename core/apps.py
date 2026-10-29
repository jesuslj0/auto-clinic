from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Clínicas y usuarios'

    def ready(self):
        from audit import registry

        from core.models import Clinic, RecoveryCode, TOTPDevice, User

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

        # La verificación en dos pasos: altas, confirmaciones y bajas de
        # dispositivos y códigos. El secreto y los hashes jamás en el log; el
        # último intervalo usado cambia en cada login y solo sería ruido.
        registry.register(TOTPDevice, sensitive=['secret'], exclude=['last_used_step'])
        registry.register(RecoveryCode, sensitive=['code_hash'])
