from django.apps import AppConfig


class AppointmentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'appointments'
    verbose_name = 'Citas y profesionales'

    def ready(self):
        import appointments.signals  # noqa: F401

        from audit import registry

        from appointments.models import (
            Appointment,
            Professional,
            ProfessionalSchedule,
            ProfessionalTimeOff,
        )

        # `AppointmentStatusHistory` NO se registra: es el log de dominio de las
        # transiciones de estado de una cita, otra cosa distinta de la auditoría.
        # Auditarlo duplicaría cada cambio de estado en las dos tablas.
        registry.register(
            Appointment,
            sensitive=['notes'],
            exclude=['confirmation_token'],
        )
        registry.register(Professional)
        registry.register(ProfessionalSchedule)
        registry.register(
            ProfessionalTimeOff,
            # `note` puede llevar el motivo de una baja: es dato de salud del
            # profesional, no del paciente, pero tampoco va al log.
            sensitive=['note'],
        )
