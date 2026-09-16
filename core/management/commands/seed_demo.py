"""Clínica de demostración con actividad realista, para capturas de pantalla.

**Temporal y solo de desarrollo.** No forma parte del producto: existe para poder
fotografiar el panel con datos que se parezcan a los de una clínica en marcha, en
vez de con las pantallas vacías de una base recién migrada.

Todo lo que siembra cuelga de una clínica propia (`demo-propus`), nunca de la que
ya tengas: así `--undo` puede borrarlo entero sin tocar nada tuyo.

Qué crea:

- la clínica, con tres profesionales (uno admin) y su horario semanal;
- el catálogo de podología: ocho categorías con sus colores y catorce servicios,
  con precios fijos y variables;
- veinte pacientes, con altas repartidas en el tiempo;
- citas en las semanas pasadas y en las próximas, con la mezcla real de estados
  (completadas, confirmadas, pendientes, canceladas y no presentados) y de
  orígenes (panel, agente de WhatsApp y reserva pública);
- la cadena clínica de cada cita completada: episodio, visita y procedimiento con
  el precio del catálogo congelado;
- la facturación que sale de ahí: facturas emitidas y cobros repartidos día a
  día, con impagadas y parciales, más procedimientos sin facturar;
- conversaciones de WhatsApp en la bandeja de chats y entradas de la base de
  conocimiento.

Las gráficas del panel leen **cobros** (`Payment.paid_at`), no el precio del
catálogo, así que sin los cobros del final de la cadena el bloque económico sale
a cero por mucha cita que haya. Por eso el sembrado llega hasta el dinero.

Uso normal:

    python manage.py seed_demo                 # seis semanas de actividad
    python manage.py seed_demo --weeks 2       # el aspecto de una clínica recién estrenada
    python manage.py seed_demo --undo          # borra la clínica demo entera

`--weeks` decide cuánta historia hay detrás. Con seis semanas el panel compara
contra el mes anterior y la gráfica mensual sale llena; con dos, el panel dice
—con razón— que no hay mes anterior con el que comparar.

La ficha clínica rica (anamnesis, mapa del pie, consentimientos) la siembra
`seed_clinical`, que este comando llama al final salvo `--skip-clinical`.

**Solo con DEBUG=True.** No es prudencia genérica: `seed_clinical` firma notas, y
una nota firmada no se puede borrar ni por ORM ni por SQL (lo impide el trigger
de `clinical/migrations/0002`). `--undo` tiene que desactivar los triggers para
limpiar, y eso no puede pasar en una base de verdad.
"""
import random
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models.signals import post_save
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import mark_session_read, record_message
from appointments.models import (
    Appointment,
    AppointmentStatusHistory,
    Professional,
    ProfessionalSchedule,
    ProfessionalTimeOff,
)
from appointments.services import AppointmentDomainError, create_appointment
from appointments.signals import broadcast_appointment_update
from audit.context import ORIGIN_COMMAND, audit_context
from billing.models import PatientInvoice, Payment
from clinical.models import (
    Episode,
    Lesion,
    MedicalHistory,
    PerformedProcedure,
    Visit,
)
from core.models import Clinic, User
from knowledge.models import ClinicKnowledgeBase
from patients.models import Patient
from services.models import DEFAULT_CATEGORIES, Service, ServiceCategory

#: La clínica que este comando crea y que `--undo` borra. Es lo único que hace
#: reversible el sembrado: todo lo demás se localiza a partir de aquí.
DEMO_CLINIC_ID = 'demo-propus'

#: Semilla fija: dos ejecuciones seguidas producen el mismo reparto de citas y
#: de cobros, así que una captura se puede repetir después de un `--undo`.
SEED = 20260911

PASSWORD = 'demo1234'


# ---------------------------------------------------------------------------
# Datos de partida
# ---------------------------------------------------------------------------

CLINIC = {
    'name': 'Clínica Podológica Propus',
    'nif': 'B12345678',
    'phone': '+34952110220',
    'email': 'hola@clinicapropus.example',
    'website': 'https://propus.ink',
    'address': 'Avenida de Andalucía, 42',
    'city': 'Málaga',
    'province': 'Málaga',
    'postal_code': '29006',
    'description': (
        'Clínica de podología general, biomecánica y cirugía ungueal. '
        'Atención presencial con cita previa y agente de WhatsApp 24 h.'
    ),
}

# (email, nombre, apellidos, rol, acepta reserva online)
STAFF = [
    ('ana.ruiz@demo.local', 'Ana', 'Ruiz Belmonte', User.Role.ADMIN, True),
    ('carlos.mena@demo.local', 'Carlos', 'Mena Ortiz', User.Role.STAFF, True),
    ('lucia.prat@demo.local', 'Lucía', 'Prat Vidal', User.Role.STAFF, False),
]

# Horario semanal por profesional, en hora LOCAL de la clínica (lo exige
# `ProfessionalSchedule`: un horario recurrente no tiene UTC). Jornada partida de
# lunes a viernes, y un sábado de mañana para que la agenda no sea un rectángulo.
SCHEDULES = {
    'ana.ruiz@demo.local': [
        (0, '09:00', '14:00'), (0, '16:00', '20:00'),
        (1, '09:00', '14:00'), (1, '16:00', '20:00'),
        (2, '09:00', '14:00'), (2, '16:00', '20:00'),
        (3, '09:00', '14:00'), (3, '16:00', '20:00'),
        (4, '09:00', '14:00'),
        (5, '10:00', '14:00'),
    ],
    'carlos.mena@demo.local': [
        (0, '10:00', '14:00'), (0, '16:00', '20:00'),
        (1, '10:00', '14:00'), (1, '16:00', '20:00'),
        (2, '16:00', '20:00'),
        (3, '10:00', '14:00'), (3, '16:00', '20:00'),
        (4, '10:00', '14:00'), (4, '16:00', '19:00'),
    ],
    'lucia.prat@demo.local': [
        (0, '09:00', '13:00'),
        (2, '09:00', '13:00'),
        (4, '09:00', '13:00'),
    ],
}

# (categoría, nombre, duración, precio, precio máximo o None)
# El precio máximo convierte el servicio en variable: es el caso que hay que
# poder enseñar en el catálogo («desde X» y «X – Y»).
CATALOG = [
    ('Quiropodia', 'Quiropodia general', 45, '38.00', None),
    ('Quiropodia', 'Quiropodia de mantenimiento', 30, '30.00', None),
    ('Onicología', 'Tratamiento de onicomicosis', 30, '40.00', None),
    ('Onicología', 'Reconstrucción ungueal', 45, '45.00', None),
    ('Biomecánica', 'Estudio biomecánico completo', 60, '80.00', None),
    ('Biomecánica', 'Revisión biomecánica', 30, '40.00', None),
    ('Ortopodología', 'Soportes plantares personalizados', 60, '180.00', '260.00'),
    ('Ortopodología', 'Revisión de soportes plantares', 30, '25.00', None),
    ('Cirugía ungueal', 'Cirugía de uña encarnada', 60, '180.00', None),
    ('Cirugía ungueal', 'Espiculectomía', 30, '45.00', None),
    ('Pie diabético', 'Revisión de pie diabético', 45, '45.00', None),
    ('Pie diabético', 'Cura de úlcera', 30, '35.00', '60.00'),
    ('Podología deportiva', 'Valoración de la pisada', 45, '60.00', None),
    ('Podología infantil', 'Revisión podológica infantil', 30, '35.00', None),
]

# Quién presta qué. Por categoría, que es como se reparte el trabajo en una
# clínica de verdad: no todo el mundo opera.
SERVICES_BY_STAFF = {
    'ana.ruiz@demo.local': [
        'Quiropodia', 'Onicología', 'Cirugía ungueal', 'Pie diabético',
    ],
    'carlos.mena@demo.local': [
        'Quiropodia', 'Biomecánica', 'Ortopodología', 'Podología deportiva',
    ],
    'lucia.prat@demo.local': [
        'Quiropodia', 'Onicología', 'Podología infantil', 'Pie diabético',
    ],
}

# (nombre, apellidos, día de nacimiento, antigüedad en días)
# La antigüedad reparte las altas: los de menos de treinta días alimentan la
# tarjeta de «nuevos pacientes este mes» del panel.
PATIENTS = [
    ('María', 'Fernández Gil', '1958-03-14', 240),
    ('José Luis', 'Ramos Vidal', '1949-11-02', 225),
    ('Carmen', 'Ortega Sanz', '1966-07-21', 198),
    ('Antonio', 'Molina Cabrera', '1954-01-30', 176),
    ('Laura', 'Iglesias Bravo', '1988-09-09', 160),
    ('Miguel Ángel', 'Soler Ruiz', '1979-05-17', 143),
    ('Pilar', 'Navarro Esteban', '1962-12-04', 130),
    ('Francisco', 'Domínguez León', '1951-08-26', 118),
    ('Nuria', 'Castaño Peña', '1993-02-11', 96),
    ('Javier', 'Herrera Lozano', '1985-06-23', 84),
    ('Beatriz', 'Cordero Marín', '1974-10-08', 71),
    ('Rafael', 'Pastor Aguilar', '1960-04-19', 63),
    ('Elena', 'Vázquez Rico', '1997-01-27', 52),
    ('Sergio', 'Benítez Cano', '1982-11-15', 44),
    ('Marta', 'Redondo Gallego', '1990-08-03', 37),
    ('Andrés', 'Villalba Nieto', '1969-03-29', 26),
    ('Cristina', 'Salas Moreno', '2014-05-12', 19),
    ('Tomás', 'Arenas Pinto', '1957-09-06', 12),
    ('Silvia', 'Maldonado Rey', '1986-12-18', 7),
    ('Óscar', 'Cuenca Ferrer', '2011-02-24', 3),
]

APPOINTMENT_NOTES = [
    '',
    '',
    '',
    'Viene acompañada por su hija.',
    'Trae el informe del endocrino.',
    'Prefiere última hora de la tarde.',
    'Avisa de que llegará diez minutos tarde.',
    'Revisión del tratamiento de la visita anterior.',
    'Derivado por su médico de familia.',
]

# Zonas tratadas, para que los procedimientos no salgan todos en el mismo sitio.
# Codificadas, nunca texto libre: es la regla de la capa clínica.
ZONES = [
    (Lesion.AnatomicalZone.HALLUX, Lesion.Laterality.LEFT),
    (Lesion.AnatomicalZone.HALLUX, Lesion.Laterality.RIGHT),
    (Lesion.AnatomicalZone.FIRST_METATARSAL, Lesion.Laterality.LEFT),
    (Lesion.AnatomicalZone.FIFTH_METATARSAL, Lesion.Laterality.RIGHT),
    (Lesion.AnatomicalZone.HEEL, Lesion.Laterality.LEFT),
    (Lesion.AnatomicalZone.HEEL, Lesion.Laterality.RIGHT),
    (Lesion.AnatomicalZone.FIFTH_TOE, Lesion.Laterality.RIGHT),
    (Lesion.AnatomicalZone.INTERDIGITAL, Lesion.Laterality.LEFT),
    (Lesion.AnatomicalZone.MEDIAL_ARCH, Lesion.Laterality.RIGHT),
]

# Conversaciones de WhatsApp. Cada tupla es (quién, texto): `patient` entra,
# `agent` contesta. Las escribe `record_message`, igual que n8n.
CONVERSATIONS = [
    [
        ('patient', 'Buenas, quería pedir cita para una quiropodia'),
        ('agent', '¡Hola! Claro. Tengo hueco el jueves a las 10:00 o el viernes a las 17:30. ¿Cuál te viene mejor?'),
        ('patient', 'El jueves a las 10 perfecto'),
        ('agent', 'Hecho. Te he reservado el jueves a las 10:00 para una quiropodia general con Ana Ruiz. Te escribo el día antes para recordártelo.'),
    ],
    [
        ('patient', 'Hola, ¿cuánto cuesta el estudio biomecánico?'),
        ('agent', 'El estudio biomecánico completo son 80 € y dura una hora. Incluye el análisis de la pisada y el informe con las recomendaciones.'),
        ('patient', '¿Y las plantillas van aparte?'),
        ('agent', 'Sí, los soportes plantares personalizados van de 180 a 260 € según el material. Se valoran en el propio estudio.'),
    ],
    [
        ('patient', 'Necesito cambiar la cita del martes'),
        ('agent', 'Sin problema. ¿Te va bien el miércoles a las 18:00 o el jueves a las 12:30?'),
        ('patient', 'Miércoles a las 6'),
        ('agent', 'Cambiada al miércoles a las 18:00. La del martes queda cancelada.'),
    ],
    [
        ('patient', 'Buenos días, mi padre es diabético y tiene una herida en el pie. ¿Podéis verle hoy?'),
        ('agent', 'Vamos a darle prioridad. Tengo un hueco esta tarde a las 16:30 con Ana Ruiz, que lleva el pie diabético. ¿Le va bien?'),
        ('patient', 'Sí, allí estaremos'),
        ('agent', 'Anotado para las 16:30. Si la herida sangra o aparece fiebre antes, acudid a urgencias sin esperar.'),
    ],
    [
        ('patient', '¿A qué hora abrís los sábados?'),
        ('agent', 'Los sábados abrimos de 10:00 a 14:00. De lunes a viernes, de 9:00 a 14:00 y de 16:00 a 20:00.'),
    ],
    [
        ('patient', 'Hola! Quería información sobre la cirugía de uña encarnada'),
        ('agent', 'La cirugía ungueal se hace con anestesia local y dura una hora. Son 180 € e incluye las curas posteriores. Antes hace falta una valoración previa, que son 38 €.'),
        ('patient', '¿Y cuánto tiempo estaré sin poder andar?'),
        ('agent', 'Se puede caminar el mismo día, con calzado ancho. Lo normal es hacer vida normal en 48 h y evitar el deporte dos semanas.'),
    ],
    [
        ('patient', 'Se me ha olvidado, ¿mi cita era hoy o mañana?'),
        ('agent', 'La tienes mañana a las 11:00 con Carlos Mena, para la revisión de los soportes plantares.'),
        ('patient', 'Genial, gracias'),
    ],
    [
        ('patient', 'Quería anular la cita del viernes, me ha surgido un viaje'),
        ('agent', 'Anulada la del viernes. Cuando quieras retomarla, escríbeme y la buscamos.'),
    ],
    [
        ('patient', 'Buenas tardes, ¿tratáis a niños?'),
        ('agent', 'Sí, hacemos revisión podológica infantil: 35 € y media hora. Es la consulta indicada para valorar la pisada y el crecimiento del pie.'),
        ('patient', 'Mi hijo tiene 9 años y camina metiendo los pies hacia dentro'),
        ('agent', 'Es motivo de consulta habitual. ¿Te reservo hueco el lunes a las 17:00 con Lucía Prat?'),
    ],
    [
        ('patient', '¿Puedo pagar con Bizum?'),
        ('agent', 'Sí, aceptamos Bizum, tarjeta, transferencia y efectivo.'),
    ],
]

KNOWLEDGE = [
    ('schedule', 'Horario de atención',
     'De lunes a viernes de 9:00 a 14:00 y de 16:00 a 20:00. Sábados de 10:00 a '
     '14:00. Domingos y festivos, cerrado. La última cita se da media hora antes '
     'del cierre.'),
    ('location', 'Cómo llegar',
     'Avenida de Andalucía, 42, 29006 Málaga. Parada de metro Príncipe de '
     'Asturias a 200 m. Aparcamiento público en la misma calle. El acceso es '
     'accesible, sin escalones.'),
    ('pricing', 'Tarifas habituales',
     'Quiropodia general 38 €. Quiropodia de mantenimiento 30 €. Estudio '
     'biomecánico completo 80 €. Soportes plantares personalizados de 180 a '
     '260 €. Cirugía de uña encarnada 180 €, curas incluidas. Revisión de pie '
     'diabético 45 €.'),
    ('services', 'Qué tratamos',
     'Podología general, onicología, biomecánica y ortopodología, cirugía '
     'ungueal, pie diabético, podología deportiva y podología infantil.'),
    ('policies', 'Cancelaciones y retrasos',
     'Las citas se pueden anular sin coste hasta 24 h antes. Por debajo de ese '
     'plazo se cobra el 50 % del servicio. Un retraso de más de 15 minutos puede '
     'obligar a reprogramar la cita.'),
    ('policies', 'Formas de pago',
     'Tarjeta, Bizum, transferencia y efectivo. Se entrega factura de todos los '
     'tratamientos. Los soportes plantares se abonan al encargarlos.'),
    ('team', 'Equipo',
     'Ana Ruiz Belmonte, podóloga, cirugía ungueal y pie diabético. Carlos Mena '
     'Ortiz, podólogo, biomecánica y ortopodología. Lucía Prat Vidal, podóloga, '
     'podología infantil y onicología.'),
    ('faq', '¿Hace falta cita previa?',
     'Sí, se atiende solo con cita previa. Se puede pedir por WhatsApp, por '
     'teléfono o desde la web. Para urgencias del pie diabético se reserva hueco '
     'el mismo día siempre que es posible.'),
    ('faq', '¿Qué llevo a la primera consulta?',
     'El calzado que uses a diario, los informes médicos que tengas y la lista de '
     'la medicación que tomas. Si vienes por un estudio biomecánico, trae también '
     'las zapatillas de deporte.'),
]


class Command(BaseCommand):
    help = (
        'Crea una clínica de demostración con actividad realista (citas, '
        'facturación, chats) para hacer capturas de pantalla. Solo desarrollo; '
        '--undo la borra entera.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--weeks', type=int, default=6,
            help='Semanas de historia hacia atrás (por defecto 6). Con 2 se ve '
                 'una clínica recién estrenada, pero el panel se queda sin mes '
                 'anterior con el que comparar.',
        )
        parser.add_argument(
            '--undo', action='store_true',
            help='Borra la clínica demo y todo lo que cuelga de ella.',
        )
        parser.add_argument(
            '--skip-clinical', action='store_true',
            help='No llama a seed_clinical al final: sin anamnesis, mapa del pie '
                 'ni consentimientos firmados.',
        )

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_demo solo se ejecuta con DEBUG=True: crea datos de mentira '
                'y su --undo desactiva los triggers de inmutabilidad clínica.'
            )

        if options['undo']:
            self._undo()
            return

        self.rng = random.Random(SEED)
        self.weeks = max(1, options['weeks'])
        self.tz = ZoneInfo(CLINIC.get('timezone', 'Europe/Madrid'))
        self.totals = {
            'pacientes': 0, 'servicios': 0, 'citas': 0, 'visitas': 0,
            'procedimientos': 0, 'facturas': 0, 'cobros': 0, 'chats': 0,
        }

        # El post_save de la cita empuja a WebSocket por Redis. Sembrando son
        # cientos de mensajes que nadie escucha, y sin Redis delante el comando
        # reventaría a mitad. Se reconecta pase lo que pase.
        post_save.disconnect(broadcast_appointment_update, sender=Appointment)
        try:
            with audit_context(origin=ORIGIN_COMMAND, user_repr='comando seed_demo'):
                clinic = self._clinic()
                professionals = self._staff(clinic)
                self._catalog(clinic, professionals)
                patients = self._patients(clinic)
                appointments = self._appointments(clinic, professionals, patients)

                # `seed_clinical` va AQUÍ, y el orden no es casual: se salta a
                # todo paciente que ya tenga episodios, así que tiene que correr
                # antes de que `_clinical_chain` abra los suyos. Y después de las
                # citas, porque cuelga sus visitas de las citas completadas que
                # encuentre: así el panel enseña la cadena cita → visita → nota.
                if not options['skip_clinical']:
                    self.stdout.write('')
                    call_command('seed_clinical', clinic=DEMO_CLINIC_ID)
                    self.stdout.write('')

                self._clinical_chain(clinic, appointments)
                self._chats(clinic, patients)
                self._knowledge(clinic)
        finally:
            post_save.connect(broadcast_appointment_update, sender=Appointment)

        self._report(clinic)

    # ------------------------------------------------------------------
    # Clínica, equipo y catálogo
    # ------------------------------------------------------------------

    def _clinic(self):
        clinic, created = Clinic.objects.get_or_create(
            clinic_id=DEMO_CLINIC_ID, defaults=CLINIC,
        )
        if not created:
            raise CommandError(
                f'La clínica «{DEMO_CLINIC_ID}» ya existe. Ejecuta primero '
                f'«python manage.py seed_demo --undo» y vuelve a sembrar.'
            )

        # Credenciales de mentira, pero con la forma de las de verdad: lo que se
        # quiere fotografiar es la pantalla de integración en estado conectado.
        clinic.whatsapp_phone_number_id = '100000000000001'
        clinic.whatsapp_token = 'EAADEMO' + 'x' * 40
        clinic.whatsapp_verify_token = 'demo-verify-token'
        clinic.save()

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{clinic.name} ({clinic.clinic_id})'
        ))
        return clinic

    def _staff(self, clinic):
        """Los tres usuarios y sus fichas de profesional, con su horario.

        La ficha no se crea aquí: la crea `ensure_professional_for_user` al
        guardar un usuario con clínica. Aquí solo se completa.
        """
        professionals = {}
        for email, first_name, last_name, role, online in STAFF:
            user = User.objects.create_user(
                email=email, password=PASSWORD,
                first_name=first_name, last_name=last_name,
                clinic=clinic, role=role,
            )
            professional = Professional.objects.get(user=user)
            professional.professional_type = Professional.ProfessionalType.PODOLOGO
            professional.accepts_online_booking = online
            professional.slot_granularity_minutes = 15
            professional.save()

            for day, start, end in SCHEDULES[email]:
                ProfessionalSchedule.objects.create(
                    professional=professional,
                    day_of_week=day,
                    start_time=start,
                    end_time=end,
                )
            professionals[email] = professional

        # Una ausencia futura, para que la agenda enseñe un hueco bloqueado que
        # no es una cita.
        start = self._local(timezone.localdate() + timedelta(days=9), 9, 0)
        ProfessionalTimeOff.objects.create(
            professional=professionals['carlos.mena@demo.local'],
            starts_at=start,
            ends_at=start + timedelta(days=4),
            reason=ProfessionalTimeOff.Reason.VACATION,
            note='Vacaciones.',
        )

        self.stdout.write(f'  {len(professionals)} profesionales con su horario.')
        return professionals

    def _catalog(self, clinic, professionals):
        # Las categorías NO se crean aquí: una clínica nueva ya nace con las
        # semillas de podología puestas (`services/signals.py`). Crearlas otra
        # vez choca contra el unique (clinic, name).
        categories = {}
        for name, color in DEFAULT_CATEGORIES:
            categories[name], _ = ServiceCategory.objects.get_or_create(
                clinic=clinic, name=name, defaults={'color': color},
            )

        by_category = {}
        for category, name, minutes, price, price_max in CATALOG:
            service = Service.objects.create(
                clinic=clinic,
                category=categories[category],
                name=name,
                duration_minutes=minutes,
                price=Decimal(price),
                price_type=(
                    Service.ValueType.VARIABLE if price_max
                    else Service.ValueType.FIXED
                ),
                price_max=Decimal(price_max) if price_max else None,
            )
            by_category.setdefault(category, []).append(service)
            self.totals['servicios'] += 1

        for email, allowed in SERVICES_BY_STAFF.items():
            offered = [s for name in allowed for s in by_category[name]]
            professionals[email].services.set(offered)

        self.stdout.write(
            f'  {len(categories)} categorías y {self.totals["servicios"]} servicios.'
        )

    # ------------------------------------------------------------------
    # Pacientes
    # ------------------------------------------------------------------

    def _patients(self, clinic):
        """Los pacientes, con su alta fechada hacia atrás.

        `created_at` es `auto_now_add`, así que solo se fija en el alta; para
        moverlo hay que escribirlo y volver a guardar. Se hace con `save()` y no
        con un `update()` masivo a propósito: `Patient` está auditado y un
        `update()` se saltaría la señal que escribe el `ChangeLog`.
        """
        today = timezone.localdate()
        patients = []
        for index, (first_name, last_name, birth, age_days) in enumerate(PATIENTS):
            patient = Patient.objects.create(
                clinic=clinic,
                first_name=first_name,
                last_name=last_name,
                email=f'paciente{index + 1:02d}@demo.local',
                phone=f'+3460011{index + 1:04d}',
                date_of_birth=birth,
            )
            patient.created_at = self._local(today - timedelta(days=age_days), 11, 30)
            patient.save()
            patients.append(patient)
            self.totals['pacientes'] += 1

        clinic.test_patient = patients[0]
        clinic.save(update_fields=['test_patient'])

        self.stdout.write(f'  {len(patients)} pacientes.')
        return patients

    # ------------------------------------------------------------------
    # Citas
    # ------------------------------------------------------------------

    def _appointments(self, clinic, professionals, patients):
        """Agenda de las últimas `weeks` semanas y de los próximos diez días.

        Las citas se crean por `create_appointment`, el mismo punto de entrada
        que usan la API y el formulario del panel: así respetan el horario del
        profesional, el solapamiento y el historial de estados. Cuando un hueco
        no cuela —porque el reparto aleatorio lo pisó— se salta y ya está.
        """
        today = timezone.localdate()
        first_day = today - timedelta(days=self.weeks * 7)
        last_day = today + timedelta(days=10)

        created = []
        day = first_day
        while day <= last_day:
            if day.weekday() != 6:
                for professional in professionals.values():
                    created += self._day_agenda(clinic, professional, day, patients, today)
            day += timedelta(days=1)

        self.totals['citas'] = len(created)
        past = [a for a in created if a.scheduled_at < timezone.now()]
        self.stdout.write(
            f'  {len(created)} citas ({len(past)} pasadas, '
            f'{len(created) - len(past)} próximas).'
        )
        return created

    def _day_agenda(self, clinic, professional, day, patients, today):
        """Llena los tramos horarios de un profesional en un día concreto."""
        offered = list(professional.services.all())
        if not offered:
            return []

        # Una agenda pasada está más llena que una futura: lo de delante todavía
        # se está llenando. Es lo que hace que el calendario se lea como una
        # clínica en marcha y no como una rejilla uniforme. El día de HOY cuenta
        # como lleno: es el que se fotografía en el panel.
        occupancy = 0.72 if day <= today else 0.45

        created = []
        for schedule in professional.schedules.filter(
            day_of_week=day.weekday(), is_active=True
        ).order_by('start_time'):
            window_end = self._local_time(day, schedule.end_time)
            cursor = self._local_time(day, schedule.start_time)

            while cursor < window_end:
                if self.rng.random() > occupancy:
                    cursor += timedelta(minutes=30)
                    continue

                service = self.rng.choice(offered)
                end = cursor + timedelta(minutes=service.booking_duration_minutes)
                if end > window_end:
                    break

                appointment = self._book(
                    clinic, professional, service, cursor, patients,
                )
                if appointment is not None:
                    created.append(appointment)
                    cursor = end + timedelta(minutes=self.rng.choice([0, 15, 15, 30]))
                else:
                    cursor += timedelta(minutes=30)

        return created

    def _book(self, clinic, professional, service, scheduled_at, patients):
        # Que una cita esté resuelta o no lo dice el RELOJ, no el calendario: a
        # media tarde, las de esta mañana ya se han dado y las de dentro de un
        # rato no. Tratar «hoy» entero como futuro deja el panel enseñando un día
        # sin una sola cita completada, que es justo lo que hay que fotografiar.
        is_past = (
            scheduled_at + timedelta(minutes=service.booking_duration_minutes)
            <= timezone.now()
        )

        patient = self.rng.choice(patients)
        # Nadie pide cita antes de existir: si el paciente se dio de alta después
        # de esa fecha, este hueco no es suyo.
        if patient.created_at > scheduled_at:
            return None

        # Casi la mitad del trabajo entra por el agente de WhatsApp: es lo que
        # vende el producto, y una agenda donde todo viene del panel lo esconde.
        source = self.rng.choices(
            [Appointment.Source.STAFF, Appointment.Source.AGENT, Appointment.Source.BOOKING],
            weights=[45, 40, 15],
        )[0]

        # Una cita del agente NACE pendiente de que la clínica la valide, y con
        # hold. Eso tiene sentido mirando hacia delante; en una cita que ya se
        # celebró significaría que nadie la validó nunca y que el hueco caducó.
        # Por eso la del pasado nace ya confirmada, sin dejar de decir de dónde
        # vino: `source` y `status` son dos ejes distintos.
        status = Appointment.Status.CONFIRMED if is_past else None

        actor = (
            AppointmentStatusHistory.Actor.PATIENT
            if source != Appointment.Source.STAFF
            else AppointmentStatusHistory.Actor.STAFF
        )

        try:
            appointment = create_appointment(
                clinic=clinic,
                scheduled_at=scheduled_at,
                require_online_booking=False,
                source=source,
                status=status,
                service=service,
                professional=professional,
                patient=patient,
                patient_name=str(patient),
                patient_phone=patient.phone,
                service_name=service.name,
                notes=self.rng.choice(APPOINTMENT_NOTES),
                actor=actor,
                actor_label=(
                    str(patient) if actor == AppointmentStatusHistory.Actor.PATIENT
                    else 'Recepción'
                ),
            )
        except AppointmentDomainError:
            return None

        # La cita se registró unos días antes de celebrarse, no hoy.
        appointment.created_at = scheduled_at - timedelta(
            days=self.rng.randint(1, 12), hours=self.rng.randint(0, 8)
        )
        appointment.save(update_fields=['created_at'])

        if is_past:
            self._resolve_past(appointment)
        else:
            self._prepare_upcoming(appointment)

        return appointment

    def _resolve_past(self, appointment):
        """Cómo acabó una cita que ya pasó."""
        roll = self.rng.random()
        when = appointment.scheduled_at
        if roll < 0.82:
            self._transition(
                appointment, Appointment.Status.COMPLETED,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label='Recepción', when=when + timedelta(hours=1),
            )
        elif roll < 0.90:
            self._transition(
                appointment, Appointment.Status.CANCELLED,
                actor=AppointmentStatusHistory.Actor.PATIENT,
                actor_label=appointment.patient_name,
                cancelled_by=Appointment.CancelledBy.PATIENT,
                when=when - timedelta(days=1),
            )
        elif roll < 0.96:
            self._transition(
                appointment, Appointment.Status.NO_SHOW,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label='Recepción', when=when + timedelta(minutes=30),
            )
        # El resto se queda confirmada y sin cerrar: también pasa.

        # Recordatorios: son citas que ya vivieron su ciclo entero.
        appointment.reminder_24h_sent = True
        appointment.reminder_24h_sent_at = when - timedelta(days=1)
        appointment.reminder_3h_sent = True
        appointment.reminder_3h_sent_at = when - timedelta(hours=3)
        if self.rng.random() < 0.7:
            appointment.reminder_responded = True
            appointment.patient_confirmed_at = when - timedelta(hours=20)
        appointment.save(update_fields=[
            'reminder_24h_sent', 'reminder_24h_sent_at', 'reminder_3h_sent',
            'reminder_3h_sent_at', 'reminder_responded', 'patient_confirmed_at',
            'updated_at',
        ])

    def _prepare_upcoming(self, appointment):
        """Lo que ya ha pasado con una cita que todavía no ha llegado."""
        hours_away = (appointment.scheduled_at - timezone.now()).total_seconds() / 3600

        # Al recordatorio de 24 h le ha dado tiempo si la cita es de mañana.
        if hours_away < 24:
            appointment.reminder_24h_sent = True
            appointment.reminder_24h_sent_at = appointment.scheduled_at - timedelta(days=1)
            if self.rng.random() < 0.6:
                appointment.reminder_responded = True
                appointment.patient_confirmed_at = timezone.now() - timedelta(hours=2)
            appointment.save(update_fields=[
                'reminder_24h_sent', 'reminder_24h_sent_at',
                'reminder_responded', 'patient_confirmed_at', 'updated_at',
            ])

        # Parte de lo que trajo el agente ya lo validó la clínica.
        if appointment.status == Appointment.Status.PENDING and self.rng.random() < 0.45:
            self._transition(
                appointment, Appointment.Status.CONFIRMED,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label='Recepción',
                when=appointment.created_at + timedelta(hours=2),
            )

    def _transition(self, appointment, status, *, actor, actor_label='',
                    cancelled_by=None, when=None):
        """Cambia el estado y lo deja escrito en el historial.

        Se escribe a mano en vez de llamar a `cancel_appointment()` y compañía
        porque esos services fechan la transición HOY, y aquí toda la historia
        está fechada hacia atrás.
        """
        previous = appointment.status
        appointment.status = status
        fields = ['status', 'updated_at']

        if cancelled_by is not None:
            appointment.cancelled_by = cancelled_by
            fields.append('cancelled_by')
        if appointment.hold_expires_at is not None:
            # Una cita validada (o resuelta) ya no caduca.
            appointment.hold_expires_at = None
            fields.append('hold_expires_at')

        appointment.save(update_fields=fields)

        entry = AppointmentStatusHistory.objects.create(
            appointment=appointment,
            from_status=previous,
            to_status=status,
            actor=actor,
            actor_label=actor_label,
        )
        if when is not None:
            # `changed_at` es auto_now_add: solo lo fija el alta, así que se
            # puede reescribir después. El historial no está auditado (lo dice
            # `appointments/apps.py`), así que no hay señal que perder.
            entry.changed_at = when
            entry.save(update_fields=['changed_at'])

    # ------------------------------------------------------------------
    # Cadena clínica y facturación
    # ------------------------------------------------------------------

    def _clinical_chain(self, clinic, appointments):
        """De la cita completada al cobro, que es lo que ven las gráficas.

        El panel suma `Payment.paid_at`, no el precio del catálogo, así que la
        cadena tiene que llegar hasta el final: visita → procedimiento →
        factura emitida → cobro. Cortarla antes deja el bloque económico a cero
        por muchas citas que haya.
        """
        completed = sorted(
            (a for a in appointments if a.status == Appointment.Status.COMPLETED),
            key=lambda a: a.scheduled_at,
        )

        # `seed_clinical` ya colgó un par de visitas de las primeras citas
        # completadas de cada paciente. Esas se dejan como están: dos visitas
        # sobre la misma cita no significan nada.
        already_visited = set(
            Visit.objects.filter(appointment__in=completed)
            .values_list('appointment_id', flat=True)
        )

        episodes = {}
        for appointment in completed:
            if appointment.pk in already_visited:
                continue
            patient = appointment.patient
            episode = episodes.get(patient.pk)
            if episode is None:
                history = MedicalHistory.objects.get(patient=patient)
                episode = Episode.objects.create(
                    history=history,
                    reason=appointment.service_name or 'Consulta podológica',
                    opened_at=appointment.scheduled_at,
                    responsible_professional=appointment.professional,
                )
                episodes[patient.pk] = episode

            visit = Visit.objects.create(
                episode=episode,
                professional=appointment.professional,
                appointment=appointment,
                occurred_at=appointment.scheduled_at,
            )
            self.totals['visitas'] += 1

            procedure = self._procedure(appointment, visit)
            self.totals['procedimientos'] += 1

            # Uno de cada seis se queda sin facturar: es la tarjeta de «trabajo
            # hecho pendiente de facturar» del panel, que es otra cosa distinta
            # de lo pendiente de cobro.
            if self.rng.random() < 0.84:
                self._invoice(clinic, appointment, procedure)

        # Algunos procesos ya se cerraron; los demás siguen abiertos. El cierre
        # va al final porque un episodio cerrado no admite visitas nuevas.
        for episode in episodes.values():
            last_visit = episode.visits.order_by('-occurred_at').first()
            if last_visit is None or self.rng.random() > 0.45:
                continue
            episode.status = Episode.Status.CLOSED
            episode.discharged_at = last_visit.occurred_at + timedelta(hours=1)
            episode.save(update_fields=['status', 'discharged_at', 'updated_at'])

        self.stdout.write(
            f'  {self.totals["visitas"]} visitas, '
            f'{self.totals["procedimientos"]} procedimientos, '
            f'{self.totals["facturas"]} facturas y {self.totals["cobros"]} cobros.'
        )

    def _procedure(self, appointment, visit):
        """El procedimiento de la visita, con el precio congelado.

        `frozen_price` se deja vacío para que lo copie del catálogo, que es el
        camino normal. La excepción es el servicio de precio variable: ahí se
        cobra por lo que se hizo, no por el mínimo de la ficha.
        """
        zone, laterality = self.rng.choice(ZONES)
        procedure = PerformedProcedure(
            visit=visit,
            service=appointment.service,
            laterality=laterality,
            affected_zone=zone,
            performed_at=appointment.scheduled_at,
            created_by=appointment.professional,
        )

        service = appointment.service
        if service.has_variable_price and service.price_max:
            low = int(service.price * 100)
            high = int(service.price_max * 100)
            # Redondeado a cinco euros: un importe cobrado tiene esta pinta.
            cents = self.rng.randrange(low, high + 1, 500)
            procedure.frozen_price = Decimal(cents) / 100

        procedure.save()
        return procedure

    def _invoice(self, clinic, appointment, procedure):
        """Emite la factura del procedimiento y le registra (o no) su cobro."""
        invoice = PatientInvoice.objects.create(
            clinic=clinic,
            patient=appointment.patient,
            created_by=appointment.professional,
        )
        invoice.add_procedure(procedure)
        invoice.issue()

        issued_at = appointment.scheduled_at + timedelta(hours=1)
        self._backdate(invoice, issued_at=issued_at)
        self.totals['facturas'] += 1

        roll = self.rng.random()
        if roll < 0.74:
            amount = invoice.total
        elif roll < 0.86:
            # Cobrada a medias: la factura queda «parcial» y sigue reclamándose.
            amount = (invoice.total * Decimal('0.5')).quantize(Decimal('0.01'))
        else:
            return  # Impagada.

        # El dinero entra el mismo día o en los tres siguientes, nunca mañana.
        paid_at = min(
            issued_at + timedelta(days=self.rng.choice([0, 0, 0, 1, 2, 3])),
            timezone.now(),
        )
        Payment.objects.create(
            clinic=clinic,
            invoice=invoice,
            amount=amount,
            method=self.rng.choices(
                [Payment.Method.CARD, Payment.Method.BIZUM,
                 Payment.Method.CASH, Payment.Method.TRANSFER],
                weights=[50, 25, 18, 7],
            )[0],
            paid_at=paid_at,
            created_by=appointment.professional,
        )
        self.totals['cobros'] += 1

    def _backdate(self, document, **fields):
        """Reescribe campos congelados de un documento ya emitido. SOLO siembra.

        Una factura emitida rechaza cualquier cambio en `FROZEN_FIELDS`, y con
        razón: es la garantía de que lo entregado al paciente no se reescribe.
        Pero `issue()` fecha la emisión HOY, y una demo necesita facturas
        repartidas por el mes. Se actualiza también la copia cargada para que el
        guardián no vea diferencia, y se guarda por `save()` —nunca por un
        `update()` masivo— para que la escritura siga dejando su `ChangeLog`.

        No copies este atajo fuera de un comando de siembra.
        """
        for name, value in fields.items():
            setattr(document, name, value)
        document._loaded_frozen = {**getattr(document, '_loaded_frozen', {}), **fields}
        document.save()

    # ------------------------------------------------------------------
    # Chats y base de conocimiento
    # ------------------------------------------------------------------

    def _chats(self, clinic, patients):
        """Hilos de WhatsApp en la bandeja, con su cabecera ya calculada.

        Se escriben con `record_message`, el mismo camino que usa n8n: es lo que
        deja bien el preview, la hora del último mensaje y el contador de no
        leídos que pinta la lista.
        """
        now = timezone.now()
        for index, script in enumerate(CONVERSATIONS):
            patient = patients[index % len(patients)]
            session, _ = ConversationSession.objects.get_or_create(
                clinic=clinic, phone=patient.phone, defaults={'patient': patient},
            )

            # Los primeros hilos son los más recientes: la bandeja ordena por
            # fecha del último mensaje.
            started = now - timedelta(
                hours=index * 7 + self.rng.randint(1, 5),
                minutes=self.rng.randint(0, 55),
            )
            for step, (who, text) in enumerate(script):
                inbound = who == 'patient'
                record_message(
                    clinic=clinic,
                    session=session,
                    direction=(
                        ChatMessage.Direction.INBOUND if inbound
                        else ChatMessage.Direction.OUTBOUND
                    ),
                    sender=(
                        ChatMessage.Sender.PATIENT if inbound
                        else ChatMessage.Sender.AGENT
                    ),
                    body=text,
                    status='' if inbound else ChatMessage.Status.READ,
                    sent_at=started + timedelta(minutes=step * self.rng.randint(1, 4)),
                )

            # Dos hilos se quedan sin leer; el resto ya los miró alguien.
            if index % 5 != 0:
                mark_session_read(session)

            # Y en uno de ellos ha entrado una persona, así que el agente calla.
            if index == 3:
                session.agent_paused = True
                session.save(update_fields=['agent_paused', 'updated_at'])

            self.totals['chats'] += 1

        self.stdout.write(f'  {self.totals["chats"]} conversaciones de WhatsApp.')

    def _knowledge(self, clinic):
        for kb_type, title, content in KNOWLEDGE:
            ClinicKnowledgeBase.objects.create(
                clinic=clinic, kb_type=kb_type, title=title, content=content,
            )
        self.stdout.write(f'  {len(KNOWLEDGE)} entradas en la base de conocimiento.')

    # ------------------------------------------------------------------
    # Salida
    # ------------------------------------------------------------------

    def _report(self, clinic):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Clínica demo lista: {clinic.name} ({clinic.clinic_id}).'
        ))
        summary = ', '.join(f'{count} {name}' for name, count in self.totals.items())
        self.stdout.write(f'  {summary}.')
        self.stdout.write('')
        self.stdout.write('  Entra en http://localhost:8000/ con:')
        for email, first_name, last_name, role, _ in STAFF:
            self.stdout.write(f'    {email} / {PASSWORD}  ({first_name} {last_name}, {role})')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            '  Para borrarlo todo: python manage.py seed_demo --undo'
        ))

    # ------------------------------------------------------------------
    # Deshacer
    # ------------------------------------------------------------------

    def _undo(self):
        """Borra la clínica demo y todo lo que cuelga de ella.

        Va en SQL y no por el ORM por dos motivos que no se pueden esquivar: una
        nota firmada tiene un trigger que prohíbe su `DELETE` (y la adenda, otro
        que prohíbe todo lo que no sea insertar), y media capa clínica usa
        `on_delete=PROTECT`, que bloquearía la cascada.

        `session_replication_role = replica` apaga en esta sesión los triggers de
        usuario Y la comprobación de claves ajenas, así que las filas se pueden
        borrar en cualquier orden. Se restaura siempre, y todo va en una
        transacción: o se borra entero o no se borra nada.

        Lo que NO toca: los registros de `audit`, que son de solo inserción por
        diseño y no tienen a qué clínica pertenecen; quedan apuntando a objetos
        que ya no existen, que es justo lo que un registro de auditoría debe
        hacer. Tampoco borra del bucket privado las fotos que hubiera subido
        `seed_clinical`.
        """
        if not Clinic.objects.filter(clinic_id=DEMO_CLINIC_ID).exists():
            self.stdout.write(f'No hay ninguna clínica «{DEMO_CLINIC_ID}» que borrar.')
            return

        ids = self._demo_ids()

        with transaction.atomic(), connection.cursor() as cursor:
            try:
                cursor.execute("SET session_replication_role = 'replica'")
            except Exception as error:  # pragma: no cover - depende del rol de BD
                raise CommandError(
                    'No se han podido desactivar los triggers para limpiar '
                    f'({error}). Hace falta un usuario de PostgreSQL con permiso '
                    'de superusuario; el de docker compose lo tiene.'
                )
            try:
                for sql, params in self._undo_statements(ids):
                    cursor.execute(sql, params)
            finally:
                cursor.execute("SET session_replication_role = 'origin'")

        self.stdout.write(self.style.SUCCESS(
            f'Borrada la clínica «{DEMO_CLINIC_ID}» y todo lo que colgaba de ella.'
        ))
        self.stdout.write(
            '  Los registros de audit/ se conservan: son de solo inserción.'
        )

    def _demo_ids(self):
        """Las claves de todo lo que cuelga de la clínica demo.

        Se resuelven aquí, con el ORM y con los managers que ven también lo
        borrado lógicamente (`all_objects`), para que las sentencias de borrado
        sean listas planas en vez de subconsultas anidadas de seis niveles.
        """
        from clinical.models import (
            ClinicalNote,
            ConsentTemplate,
            ConsentVersion,
            LesionObservation,
            Question,
            QuestionnaireTemplate,
            TemplateVersion,
        )

        clinic = DEMO_CLINIC_ID
        patients = list(
            Patient.objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )
        professionals = list(
            Professional.objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )
        users = list(
            User.objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )
        histories = list(
            MedicalHistory.all_objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )
        episodes = list(
            Episode.all_objects.filter(history_id__in=histories).values_list('id', flat=True)
        )
        visits = list(
            Visit.all_objects.filter(episode_id__in=episodes).values_list('id', flat=True)
        )
        notes = list(
            ClinicalNote.all_objects.filter(visit_id__in=visits).values_list('id', flat=True)
        )
        lesions = list(
            Lesion.all_objects.filter(episode_id__in=episodes).values_list('id', flat=True)
        )
        observations = list(
            LesionObservation.all_objects.filter(lesion_id__in=lesions)
            .values_list('id', flat=True)
        )
        questionnaires = list(
            QuestionnaireTemplate.all_objects.filter(clinic_id=clinic)
            .values_list('id', flat=True)
        )
        versions = list(
            TemplateVersion.all_objects.filter(template_id__in=questionnaires)
            .values_list('id', flat=True)
        )
        questions = list(
            Question.all_objects.filter(version_id__in=versions).values_list('id', flat=True)
        )
        consents = list(
            ConsentTemplate.all_objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )
        consent_versions = list(
            ConsentVersion.all_objects.filter(template_id__in=consents)
            .values_list('id', flat=True)
        )
        appointments = list(
            Appointment.objects.filter(clinic_id=clinic).values_list('id', flat=True)
        )

        return {
            'clinic': clinic,
            'patients': patients,
            'professionals': professionals,
            'users': users,
            'histories': histories,
            'episodes': episodes,
            'visits': visits,
            'notes': notes,
            'lesions': lesions,
            'observations': observations,
            'questionnaires': questionnaires,
            'versions': versions,
            'questions': questions,
            'consents': consents,
            'consent_versions': consent_versions,
            'appointments': appointments,
        }

    def _undo_statements(self, ids):
        """`(sql, params)` de cada borrado, de las hojas hacia la raíz.

        Los nombres de tabla salen de `Model._meta.db_table` y no escritos a
        mano: así renombrar una tabla no deja aquí una sentencia muerta que
        falle en silencio.
        """
        from clinical.models import (
            Addendum,
            ClinicalAlert,
            ClinicalNote,
            ConsentTemplate,
            ConsentVersion,
            LesionAttachment,
            LesionObservation,
            Question,
            QuestionnaireResponse,
            QuestionnaireTemplate,
            SignedConsent,
            TemplateVersion,
        )
        from clinical.models import HistorySequence
        from billing.models import InvoiceSequence, ReceiptSequence

        def table(model):
            return model._meta.db_table

        clinic = ids['clinic']
        statements = [
            # --- Agente y conocimiento -----------------------------------
            (f'DELETE FROM {table(ChatMessage)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ConversationSession)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ClinicKnowledgeBase)} WHERE clinic_id = %s', [clinic]),

            # --- Facturación ---------------------------------------------
            (f'DELETE FROM {table(Payment)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(PatientInvoice)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(InvoiceSequence)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ReceiptSequence)} WHERE clinic_id = %s', [clinic]),

            # --- Capa clínica --------------------------------------------
            (f'DELETE FROM {table(LesionAttachment)} WHERE observation_id = ANY(%s)',
             [ids['observations']]),
            (f'DELETE FROM {table(LesionObservation)} WHERE lesion_id = ANY(%s)',
             [ids['lesions']]),
            (f'DELETE FROM {table(Lesion)} WHERE episode_id = ANY(%s)', [ids['episodes']]),
            (f'DELETE FROM {table(Addendum)} WHERE note_id = ANY(%s)', [ids['notes']]),
            (f'DELETE FROM {table(ClinicalNote)} WHERE visit_id = ANY(%s)', [ids['visits']]),
            (f'DELETE FROM {table(PerformedProcedure)} WHERE visit_id = ANY(%s)',
             [ids['visits']]),
            (f'DELETE FROM {table(Visit)} WHERE episode_id = ANY(%s)', [ids['episodes']]),
            (f'DELETE FROM {table(SignedConsent)} WHERE patient_id = ANY(%s)',
             [ids['patients']]),
            (f'DELETE FROM {table(ConsentVersion)} WHERE template_id = ANY(%s)',
             [ids['consents']]),
            (f'DELETE FROM {table(ConsentTemplate)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ClinicalAlert)} WHERE patient_id = ANY(%s)',
             [ids['patients']]),
            (f'DELETE FROM {table(QuestionnaireResponse)} WHERE patient_id = ANY(%s)',
             [ids['patients']]),
            (f'DELETE FROM {table(Question)} WHERE version_id = ANY(%s)', [ids['versions']]),
            (f'DELETE FROM {table(TemplateVersion)} WHERE template_id = ANY(%s)',
             [ids['questionnaires']]),
            (f'DELETE FROM {table(QuestionnaireTemplate)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(Episode)} WHERE history_id = ANY(%s)', [ids['histories']]),
            (f'DELETE FROM {table(MedicalHistory)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(HistorySequence)} WHERE clinic_id = %s', [clinic]),

            # --- Agenda ---------------------------------------------------
            (f'DELETE FROM {table(AppointmentStatusHistory)} WHERE appointment_id = ANY(%s)',
             [ids['appointments']]),
            (f'DELETE FROM {table(Appointment)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ProfessionalTimeOff)} WHERE professional_id = ANY(%s)',
             [ids['professionals']]),
            (f'DELETE FROM {table(ProfessionalSchedule)} WHERE professional_id = ANY(%s)',
             [ids['professionals']]),
            (f'DELETE FROM {Professional.services.through._meta.db_table} '
             f'WHERE professional_id = ANY(%s)', [ids['professionals']]),
            (f'DELETE FROM {table(Professional)} WHERE clinic_id = %s', [clinic]),

            # --- Catálogo, pacientes y equipo -----------------------------
            (f'DELETE FROM {table(Service)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(ServiceCategory)} WHERE clinic_id = %s', [clinic]),
            # El paciente de prueba apunta a un paciente: hay que soltarlo antes
            # de borrarlo, o la clínica se queda apuntando a una fila muerta.
            (f'UPDATE {table(Clinic)} SET test_patient_id = NULL WHERE clinic_id = %s',
             [clinic]),
            (f'DELETE FROM {table(Patient)} WHERE clinic_id = %s', [clinic]),
            (f'DELETE FROM {table(User)} WHERE id = ANY(%s)', [ids['users']]),
            (f'DELETE FROM {table(Clinic)} WHERE clinic_id = %s', [clinic]),
        ]
        return statements

    # ------------------------------------------------------------------
    # Utilidades de tiempo
    # ------------------------------------------------------------------

    def _local(self, day, hour, minute):
        """Un instante del día, en hora local de la clínica.

        El horario de la clínica es local (`ProfessionalSchedule` lo dice
        explícitamente); lo que se guarda es el mismo instante en UTC, y de la
        conversión se encarga el propio `datetime` con el `ZoneInfo`.
        """
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.tz)

    def _local_time(self, day, at):
        return datetime.combine(day, at, tzinfo=self.tz)
