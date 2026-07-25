from django.apps import AppConfig


class ClinicalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'clinical'
    verbose_name = 'Historia clínica'

    def ready(self):
        from django.db.models.signals import post_save

        from audit import registry
        from patients.models import Patient

        from clinical.models import (
            Addendum,
            ClinicalNote,
            Episode,
            MedicalHistory,
            Visit,
        )
        from clinical.signals import create_medical_history

        # --- Auditoría de cambios --------------------------------------------
        # Todo el texto clínico va como `sensitive`: el log registra QUE cambió,
        # nunca su valor. El log no puede ser una segunda copia sin cifrar de la
        # historia. El `patient_resolver` sube por la cadena hasta el paciente.
        registry.register(
            MedicalHistory,
            patient_resolver=lambda h: h.patient,
        )
        registry.register(
            Episode,
            sensitive=['reason'],
            patient_resolver=lambda e: e.history.patient,
        )
        registry.register(
            Visit,
            patient_resolver=lambda v: v.episode.history.patient,
        )
        registry.register(
            ClinicalNote,
            sensitive=['subjective', 'objective', 'assessment', 'plan'],
            patient_resolver=lambda n: n.visit.episode.history.patient,
        )
        registry.register(
            Addendum,
            sensitive=['text'],
            patient_resolver=lambda a: a.note.visit.episode.history.patient,
        )

        # --- Auto-creación de la historia al alta del paciente ---------------
        post_save.connect(
            create_medical_history,
            sender=Patient,
            dispatch_uid='clinical:create_medical_history',
        )
