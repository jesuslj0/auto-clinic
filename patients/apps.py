from django.apps import AppConfig


class PatientsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'patients'
    verbose_name = 'Pacientes'

    def ready(self):
        from audit import registry

        from patients.models import Guardian, Patient, PatientGuardian

        # `notes`, `date_of_birth` y `photo` son sensibles: del resto de campos se guarda
        # el antes/después, de estos solo que cambiaron.
        registry.register(Patient, sensitive=['notes', 'date_of_birth', 'photo'])
        registry.register(Guardian, sensitive=['notes'])
        registry.register(PatientGuardian)
