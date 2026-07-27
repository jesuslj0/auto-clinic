"""Datos de ejemplo de la capa clínica, para desarrollo.

Rellena la historia de los pacientes que ya existan en la base de datos: episodio
cerrado con nota firmada y adenda, episodio abierto con nota en borrador, y un
cuestionario de anamnesis en dos versiones con sus respuestas.

**Solo desarrollo.** Se niega a ejecutarse si `DEBUG` es `False`. El motivo no es
la prudencia genérica: una nota firmada NO se puede borrar —ni por ORM, ni por
SQL, lo impide el trigger de la migración 0002—, así que un sembrado en la base
equivocada se queda ahí para siempre.

Es repetible: los pacientes que ya tengan actividad clínica se saltan, y el
cuestionario se reutiliza si ya está creado. Con `--force` siembra igualmente
(y entonces sí duplica episodios).
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from appointments.models import Appointment
from audit.context import ORIGIN_COMMAND, audit_context
from clinical.models import (
    Addendum,
    ClinicalNote,
    Episode,
    MedicalHistory,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    Visit,
)

TEMPLATE_NAME = 'Anamnesis dental'

# Cuestionario v1. `(texto, tipo, obligatoria, opciones)`, en orden.
V1_QUESTIONS = [
    ('¿Padece alguna enfermedad crónica (diabetes, hipertensión, cardiopatía)?',
     Question.AnswerType.BOOLEAN, True, []),
    ('¿Qué medicación toma actualmente?',
     Question.AnswerType.TEXT, False, []),
    ('¿Es alérgico a algún medicamento?',
     Question.AnswerType.BOOLEAN, True, []),
    ('¿A cuáles?',
     Question.AnswerType.TEXT, False, []),
    ('¿Ha tenido alguna reacción a la anestesia local?',
     Question.AnswerType.BOOLEAN, False, []),
    ('¿Con qué frecuencia se cepilla los dientes?',
     Question.AnswerType.SINGLE_CHOICE, True,
     ['Una vez al día', 'Dos veces al día', 'Tres o más veces al día']),
    ('¿Le sangran las encías al cepillarse?',
     Question.AnswerType.SINGLE_CHOICE, False,
     ['Nunca', 'A veces', 'A menudo']),
    ('¿Fuma?',
     Question.AnswerType.BOOLEAN, False, []),
]

# Lo que añade la v2. Es lo que hace visible el versionado: las respuestas de la
# v1 siguen teniendo ocho preguntas, no diez.
V2_EXTRA_QUESTIONS = [
    ('¿Está embarazada o en periodo de lactancia?',
     Question.AnswerType.BOOLEAN, False, []),
    ('¿Cuántas piezas se ha extraído?',
     Question.AnswerType.NUMBER, False, []),
]

# Casos clínicos de ejemplo. Se reparten en rueda entre los pacientes, para que
# no salgan todas las historias con el mismo texto.
CASES = [
    {
        'closed': {
            'reason': 'Revisión anual y limpieza',
            'subjective': 'Refiere sensibilidad al frío en el lado derecho desde hace un mes.',
            'objective': 'Sarro supragingival generalizado. Sensibilidad a la percusión en 1.6. '
                         'No se observan caries en la exploración visual.',
            'assessment': 'Gingivitis leve por acúmulo de cálculo. Hipersensibilidad dentinaria en 1.6.',
            'plan': 'Tartrectomía completa. Pasta desensibilizante dos veces al día y revisión en seis meses.',
            'addendum': 'Se entrega presupuesto de férula de descarga; queda pendiente de confirmación.',
        },
        'open': {
            'reason': 'Dolor en molar inferior izquierdo',
            'subjective': 'Dolor pulsátil de tres días de evolución que aumenta con el calor y por la noche.',
            'objective': 'Caries oclusal profunda en 3.6. Prueba de vitalidad positiva y prolongada.',
            'assessment': 'Pulpitis irreversible en 3.6.',
            'plan': 'Endodoncia en 3.6, pendiente de programar. Ibuprofeno 600 mg cada 8 horas si dolor.',
        },
        # Respuestas indexadas por posición de la pregunta en la versión.
        'answers': {0: False, 1: 'Ninguna', 2: False, 4: False,
                    5: 'Dos veces al día', 6: 'A veces', 7: True},
    },
    {
        'closed': {
            'reason': 'Primera visita: revisión general',
            'subjective': 'Acude por revisión. No refiere dolor. Última visita al dentista hace más de tres años.',
            'objective': 'Higiene deficiente con tinción extrínseca. Caries incipiente en 2.5. '
                         'Ausencia de 4.6 sin rehabilitar.',
            'assessment': 'Caries incipiente en 2.5. Edentulismo parcial en sector inferior derecho.',
            'plan': 'Obturación de 2.5. Se explican opciones de rehabilitación del 4.6 y se cita para presupuesto.',
            'addendum': 'Se corrige la nomenclatura de la pieza ausente: es el 4.6, no el 4.7.',
        },
        'open': {
            'reason': 'Sangrado de encías',
            'subjective': 'Refiere sangrado al cepillarse desde hace varias semanas y mal sabor de boca.',
            'objective': 'Índice de placa alto. Sangrado al sondaje generalizado, bolsas de 4 mm en sector posterior.',
            'assessment': 'Periodontitis inicial.',
            'plan': 'Raspado y alisado radicular por cuadrantes. Instrucciones de higiene y revisión a las cuatro semanas.',
        },
        'answers': {0: True, 1: 'Metformina 850 mg', 2: True, 3: 'Penicilina', 4: False,
                    5: 'Una vez al día', 6: 'A menudo', 7: False},
    },
]


class Command(BaseCommand):
    help = (
        'Crea datos clínicos de ejemplo (episodios, visitas, notas SOAP y '
        'anamnesis) para los pacientes ya existentes. Solo para desarrollo.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clinic',
            help='clinic_id de una clínica concreta. Por defecto, todas.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No escribe nada; solo informa de lo que crearía.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Siembra también los pacientes que ya tengan actividad clínica.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_clinical solo se ejecuta con DEBUG=True: es un comando de '
                'desarrollo y lo que crea (notas firmadas) no se puede borrar.'
            )

        from core.models import Clinic

        clinics = Clinic.objects.all()
        if options['clinic']:
            clinics = clinics.filter(clinic_id=options['clinic'])
            if not clinics.exists():
                raise CommandError(f'No existe la clínica «{options["clinic"]}».')

        self.dry_run = options['dry_run']
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribe nada.'))

        totals = {'historias': 0, 'episodios': 0, 'visitas': 0, 'notas': 0,
                  'adendas': 0, 'respuestas': 0}

        # Todo lo que se cree queda en el ChangeLog atribuido al comando, no a un
        # usuario fantasma.
        with audit_context(origin=ORIGIN_COMMAND, user_repr='comando seed_clinical'):
            for clinic in clinics:
                self._seed_clinic(clinic, options['force'], totals)

        self.stdout.write('')
        summary = ', '.join(f'{count} {name}' for name, count in totals.items())
        verb = 'Se crearía' if self.dry_run else 'Creado'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {summary}.'))

    # ------------------------------------------------------------------
    # Por clínica
    # ------------------------------------------------------------------

    def _seed_clinic(self, clinic, force, totals):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{clinic.name} ({clinic.clinic_id})'))

        professional = clinic.professionals.filter(is_active=True).first()
        if professional is None:
            self.stdout.write(self.style.WARNING(
                '  Sin profesionales activos: una visita necesita uno. Se salta.'
            ))
            return

        patients = list(clinic.patients.order_by('pk'))
        if not patients:
            self.stdout.write(self.style.WARNING('  Sin pacientes. Se salta.'))
            return

        versions = self._ensure_questionnaire(clinic)

        for index, patient in enumerate(patients):
            self._seed_patient(patient, professional, versions, index, force, totals)

    def _ensure_questionnaire(self, clinic):
        """Cuestionario de anamnesis con v1 y v2 publicadas. Devuelve `(v1, v2)`.

        Si ya existe, se reutiliza tal cual: republicar versiones en cada
        ejecución llenaría el histórico de ruido.
        """
        existing = QuestionnaireTemplate.objects.filter(
            clinic=clinic, name=TEMPLATE_NAME
        ).first()
        if existing is not None:
            published = list(existing.versions.filter(is_published=True).order_by('number'))
            if published:
                self.stdout.write(f'  Cuestionario «{TEMPLATE_NAME}» ya existe: se reutiliza.')
                return published[0], published[-1]

        if self.dry_run:
            self.stdout.write(
                f'  Crearía el cuestionario «{TEMPLATE_NAME}» con dos versiones '
                f'({len(V1_QUESTIONS)} y {len(V1_QUESTIONS) + len(V2_EXTRA_QUESTIONS)} preguntas).'
            )
            return None, None

        with transaction.atomic():
            template = existing or QuestionnaireTemplate.objects.create(
                clinic=clinic, name=TEMPLATE_NAME, specialty='odontología general',
            )

            v1 = template.new_draft_version(copy_questions_from=False)
            self._add_questions(v1, V1_QUESTIONS, start_order=1)
            v1.publish()

            # La v2 clona la vigente y añade preguntas: así es como se "edita" un
            # cuestionario publicado.
            v2 = template.new_draft_version()
            self._add_questions(v2, V2_EXTRA_QUESTIONS, start_order=len(V1_QUESTIONS) + 1)
            v2.publish()

        self.stdout.write(
            f'  Cuestionario «{TEMPLATE_NAME}»: v1 ({len(V1_QUESTIONS)} preguntas) '
            f'y v2 ({len(V1_QUESTIONS) + len(V2_EXTRA_QUESTIONS)}, vigente).'
        )
        return v1, v2

    def _add_questions(self, version, specs, start_order):
        for offset, (text, answer_type, required, options) in enumerate(specs):
            Question.objects.create(
                version=version,
                text=text,
                answer_type=answer_type,
                order=start_order + offset,
                is_required=required,
                options=list(options),
            )

    # ------------------------------------------------------------------
    # Por paciente
    # ------------------------------------------------------------------

    def _seed_patient(self, patient, professional, versions, index, force, totals):
        name = f'{patient.first_name} {patient.last_name}'
        history = MedicalHistory.all_objects.filter(patient=patient).first()

        if history is not None and history.episodes.exists() and not force:
            self.stdout.write(f'  {name}: ya tiene historia con episodios. Se salta.')
            return

        case = CASES[index % len(CASES)]
        # Se cuelgan las visitas de citas reales cuando las hay: es lo que hace
        # que el panel enseñe la cadena cita → visita → nota.
        appointments = list(
            patient.appointments.filter(
                status=Appointment.Status.COMPLETED
            ).order_by('scheduled_at')
        )
        now = timezone.now()
        first_at = appointments[0].scheduled_at if appointments else now - timedelta(days=90)
        second_at = appointments[1].scheduled_at if len(appointments) > 1 else now - timedelta(days=5)

        if self.dry_run:
            missing = ' (crearía su historia)' if history is None else ''
            self.stdout.write(
                f'  {name}{missing}: 2 episodios, 2 visitas, 2 notas '
                f'(1 firmada), 1 adenda y 1 respuesta de anamnesis.'
            )
            if history is None:
                totals['historias'] += 1
            totals['episodios'] += 2
            totals['visitas'] += 2
            totals['notas'] += 2
            totals['adendas'] += 1
            totals['respuestas'] += 1
            return

        with transaction.atomic():
            if history is None:
                # Los pacientes dados de alta antes de que existiese la app
                # `clinical` no pasaron por la señal que crea la historia.
                history = MedicalHistory.objects.create(patient=patient, clinic=patient.clinic)
                totals['historias'] += 1

            # --- Episodio cerrado, con nota firmada y adenda ------------------
            closed = case['closed']
            episode = Episode.objects.create(
                history=history,
                reason=closed['reason'],
                opened_at=first_at,
                responsible_professional=professional,
            )
            visit = Visit.objects.create(
                episode=episode,
                professional=professional,
                appointment=appointments[0] if appointments else None,
                occurred_at=first_at,
            )
            note = ClinicalNote.objects.create(
                visit=visit,
                subjective=closed['subjective'],
                objective=closed['objective'],
                assessment=closed['assessment'],
                plan=closed['plan'],
            )
            note.sign(professional)
            Addendum.objects.create(note=note, author=professional, text=closed['addendum'])

            # El alta se pone a mano y no con `close()`, que fecharía el episodio
            # hoy y dejaría una historia con fechas incoherentes.
            episode.status = Episode.Status.CLOSED
            episode.discharged_at = first_at + timedelta(hours=1)
            episode.save(update_fields=['status', 'discharged_at', 'updated_at'])

            # --- Episodio abierto, con nota en borrador -----------------------
            open_case = case['open']
            episode_open = Episode.objects.create(
                history=history,
                reason=open_case['reason'],
                opened_at=second_at,
                responsible_professional=professional,
            )
            visit_open = Visit.objects.create(
                episode=episode_open,
                professional=professional,
                appointment=appointments[1] if len(appointments) > 1 else None,
                occurred_at=second_at,
            )
            ClinicalNote.objects.create(
                visit=visit_open,
                subjective=open_case['subjective'],
                objective=open_case['objective'],
                assessment=open_case['assessment'],
                plan=open_case['plan'],
            )

            totals['episodios'] += 2
            totals['visitas'] += 2
            totals['notas'] += 2
            totals['adendas'] += 1

            # --- Anamnesis ----------------------------------------------------
            if self._record_anamnesis(patient, episode_open, professional, versions,
                                      case, index, second_at):
                totals['respuestas'] += 1

        self.stdout.write(
            f'  {name}: historia {history.number}, episodio cerrado '
            f'«{closed["reason"]}» (nota firmada + adenda) y episodio abierto '
            f'«{open_case["reason"]}» (borrador).'
        )

    def _record_anamnesis(self, patient, episode, professional, versions, case, index, filled_at):
        v1, v2 = versions
        if v1 is None:
            return False

        # Se alternan versión y canal a propósito: el panel enseña así una
        # respuesta sobre una versión antigua junto a otra sobre la vigente.
        if index % 2 == 0:
            version, source, created_by = v1, QuestionnaireResponse.Source.PROFESSIONAL, professional
        else:
            version, source, created_by = v2, QuestionnaireResponse.Source.PATIENT_WEB, None

        questions = list(version.questions.all())
        answers = {
            questions[position].pk: value
            for position, value in case['answers'].items()
            if position < len(questions)
        }
        QuestionnaireResponse.record(
            version=version,
            patient=patient,
            episode=episode,
            answers=answers,
            source=source,
            created_by=created_by,
            filled_at=filled_at,
        )
        return True
