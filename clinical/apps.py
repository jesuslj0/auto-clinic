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
            ClinicalAlert,
            ClinicalNote,
            ConsentTemplate,
            ConsentVersion,
            Episode,
            Lesion,
            LesionAttachment,
            LesionObservation,
            MedicalHistory,
            PerformedProcedure,
            Question,
            QuestionnaireResponse,
            QuestionnaireTemplate,
            SignedConsent,
            TemplateVersion,
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

        # Anamnesis. El cuestionario y sus versiones NO son dato de paciente:
        # son el documento en blanco, y su historial de cambios interesa entero
        # (quién publicó qué versión y cuándo). La respuesta sí lo es: su
        # `snapshot` lleva texto clínico literal y va como sensible.
        registry.register(QuestionnaireTemplate)
        registry.register(TemplateVersion)
        registry.register(Question)
        registry.register(
            QuestionnaireResponse,
            sensitive=['snapshot'],
            patient_resolver=lambda r: r.patient,
        )

        # Alertas. El detalle libre (`note`) es dato clínico y va enmascarado;
        # el tipo, la gravedad y el alta/baja de la alerta se registran enteros,
        # que es lo que hay que poder reconstruir: qué se sabía y desde cuándo.
        registry.register(
            ClinicalAlert,
            sensitive=['note'],
            patient_resolver=lambda a: a.patient,
        )

        # Lesiones. Todo son códigos y coordenadas, no texto libre: se registran
        # enteros, que es lo que permite reconstruir la evolución de una lesión
        # (cuándo se detectó, cuándo se resolvió) sin abrir la ficha.
        registry.register(
            Lesion,
            patient_resolver=lambda lesion: lesion.episode.history.patient,
        )

        # Seguimiento de la lesión. La descripción es texto clínico y va
        # enmascarada; las medidas no, y a propósito: son el dato con el que se
        # reconstruye la evolución, y de nada sirve saber que «cambió el largo».
        registry.register(
            LesionObservation,
            sensitive=['description'],
            patient_resolver=lambda obs: obs.lesion.episode.history.patient,
        )
        # Adjuntos. La clave del objeto (`file`) va como sensible: es lo que
        # señala dónde vive la foto de un paciente, y el log no puede ser el
        # índice del bucket. Lo que sí queda entero es que se subió un adjunto,
        # de qué tipo, tamaño y por qué canal.
        registry.register(
            LesionAttachment,
            sensitive=['file'],
            patient_resolver=lambda att: att.patient,
        )

        # Procedimientos realizados. Nada de esto es texto libre —el nombre del
        # servicio sale del catálogo y la zona es un código—, así que se registra
        # entero: el importe congelado y qué se hizo son justo lo que hay que
        # poder reconstruir si mañana se discute una factura.
        registry.register(
            PerformedProcedure,
            patient_resolver=lambda proc: proc.visit.episode.history.patient,
        )

        # Consentimiento informado. Mismo criterio que la anamnesis: el documento
        # en blanco (plantilla y versiones) NO es dato de paciente y se audita
        # entero —quién publicó qué texto y cuándo es justo lo que hay que poder
        # reconstruir—. La firma sí lo es: su `text_copy` es el documento que
        # aceptó el paciente y la clave del fichero señala dónde vive su firma;
        # los dos van enmascarados. Lo que queda entero es QUE firmó y cuándo.
        registry.register(ConsentTemplate)
        registry.register(ConsentVersion)
        registry.register(
            SignedConsent,
            sensitive=['text_copy', 'signature_image'],
            patient_resolver=lambda consent: consent.patient,
        )

        # --- Auto-creación de la historia al alta del paciente ---------------
        post_save.connect(
            create_medical_history,
            sender=Patient,
            dispatch_uid='clinical:create_medical_history',
        )
