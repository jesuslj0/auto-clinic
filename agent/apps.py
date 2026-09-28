from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agent'
    verbose_name = 'Agente de WhatsApp'

    def ready(self):
        from audit import registry

        from agent.models import AgentProfile, ChatAttachment, ChatMessage

        # Con conversaciones reales, el hilo contiene texto de salud de
        # pacientes: se audita como dato clínico. `body` y `raw` son sensibles,
        # así que el log recoge QUE hubo un mensaje, nunca lo que decía.
        #
        # `read_at` queda fuera a propósito: es un acuse de lectura del panel,
        # no contenido, y `mark_session_read()` lo sella en bloque con
        # `queryset.update()`. Excluido, ese `update()` no se salta nada que la
        # auditoría fuera a registrar.
        registry.register(
            ChatMessage,
            sensitive=['body', 'raw'],
            exclude=['read_at'],
            patient_resolver=lambda message: message.session.patient,
        )
        registry.register(
            ChatAttachment,
            patient_resolver=lambda attachment: attachment.message.session.patient,
        )
        # No es dato clínico, pero cambia lo que el bot dice a los pacientes:
        # «¿quién cambió el tono el martes?» tiene que tener respuesta.
        registry.register(AgentProfile)
