"""Siembra fichas de paciente en una clínica, de forma repetible.

Pensado para tener un directorio con volumen —probar filtros, paginación,
búsqueda, o hacer capturas— sin escribir fichas a mano y sin arrastrar la
clínica entera de `seed_demo`, que siembra su propio mundo (profesionales,
servicios, citas, facturas) y no sirve para rellenar una clínica que ya existe.

Todo lo que crea queda marcado por el dominio del correo (`--email-domain`,
`seed.local` por defecto) y por el prefijo del teléfono. Ese marcaje es lo que
permite `--undo`: se borra el lote y solo el lote, nunca un paciente real.

    python manage.py seed_patients --clinic mi-clinica --count 50
    python manage.py seed_patients --clinic mi-clinica --count 50 --seed 7
    python manage.py seed_patients --clinic mi-clinica --dry-run
    python manage.py seed_patients --clinic mi-clinica --undo

Con la misma `--seed` salen exactamente los mismos pacientes, así que un lote se
puede reproducir en otra máquina o volver a sembrar tras un `--undo`.

**Por qué fila a fila y no `bulk_create`.** `Patient` está registrado en `audit`
(`patients/apps.py`) y las operaciones masivas se saltan las señales: el alta no
dejaría línea en el `ChangeLog`. El coste de ir de una en una es el precio de
que el rastro exista, y da igual que quien escriba sea un comando. Por lo mismo
`--undo` borra instancia a instancia. Todo el lote queda atribuido al comando
(`origin='command'`).

Cada paciente se guarda dos veces: el alta y, acto seguido, el retoque de
`created_at` —que es `auto_now_add` y solo se fija al insertar— para repartir
las altas hacia atrás (`--spread-days`) y que el panel tenga historia. Eso deja
un `UPDATE` extra en el `ChangeLog` por paciente; es ruido conocido de datos de
prueba, no un efecto que haya que esconder.
"""
from __future__ import annotations

import random
import unicodedata
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.context import ORIGIN_COMMAND, audit_context
from core.models import Clinic
from patients.models import Patient
from patients.services import create_patient

# Bloque de numeración reservado al sembrado: +34 600 90 XXXX. Es un móvil
# español válido para `normalize_phone`, y al ser un rango fijo se distingue de
# un teléfono real de un vistazo.
PHONE_PREFIX = '+3460090'
PHONE_DIGITS = 4
MAX_PATIENTS = 10 ** PHONE_DIGITS

FEMALE_NAMES = [
    'María', 'Carmen', 'Josefa', 'Isabel', 'Ana', 'Dolores', 'Pilar', 'Teresa',
    'Rosa', 'Cristina', 'Laura', 'Marta', 'Elena', 'Lucía', 'Nuria', 'Beatriz',
    'Silvia', 'Raquel', 'Sara', 'Patricia', 'Alicia', 'Irene', 'Andrea',
    'Natalia', 'Julia', 'Rocío', 'Susana', 'Inmaculada', 'Montserrat', 'Eva',
]
MALE_NAMES = [
    'Antonio', 'José', 'Manuel', 'Francisco', 'Juan', 'Miguel', 'Ángel',
    'Rafael', 'Javier', 'Carlos', 'Sergio', 'Andrés', 'Tomás', 'Óscar',
    'Alberto', 'Pablo', 'Daniel', 'Alejandro', 'Fernando', 'Jorge', 'Luis',
    'Ramón', 'Vicente', 'Ignacio', 'Rubén', 'Adrián', 'Gonzalo', 'Emilio',
    'Salvador', 'Joaquín',
]
SURNAMES = [
    'García', 'Fernández', 'González', 'Rodríguez', 'López', 'Martínez',
    'Sánchez', 'Pérez', 'Gómez', 'Martín', 'Jiménez', 'Ruiz', 'Hernández',
    'Díaz', 'Moreno', 'Álvarez', 'Muñoz', 'Romero', 'Alonso', 'Gutiérrez',
    'Navarro', 'Torres', 'Domínguez', 'Vázquez', 'Ramos', 'Gil', 'Ramírez',
    'Serrano', 'Blanco', 'Molina', 'Morales', 'Suárez', 'Ortega', 'Delgado',
    'Castro', 'Ortiz', 'Rubio', 'Marín', 'Sanz', 'Núñez', 'Iglesias', 'Medina',
    'Garrido', 'Cortés', 'Castillo', 'Santos', 'Lozano', 'Guerrero', 'Cano',
    'Prieto', 'Méndez', 'Cruz', 'Herrera', 'Peña', 'Flores', 'Cabrera',
    'Campos', 'Vega', 'Fuentes', 'Carrasco', 'Caballero', 'Reyes', 'Aguilar',
]

# (edad mínima, edad máxima, peso). El reparto imita el de una consulta de
# podología: mucho mayor de cincuenta, algún niño, poca gente joven.
AGE_BANDS = [(3, 14, 6), (15, 29, 9), (30, 49, 22), (50, 69, 36), (70, 93, 27)]

NOTES = [
    'Diabética tipo 2, control anual.',
    'Alergia a la penicilina.',
    'Prefiere cita a primera hora.',
    'Viene acompañado por un familiar.',
    'Usa plantillas desde 2019.',
    'Anticoagulado (Sintrom).',
    'Movilidad reducida, necesita rampa.',
    'Derivado por su médico de familia.',
    'Trabaja de pie muchas horas.',
    'Practica running, unos 40 km semanales.',
]


def _slug(text: str) -> str:
    """Quita tildes y deja solo minúsculas y puntos, para el correo."""
    plain = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return ''.join(c for c in plain.lower() if c.isalnum())


class Command(BaseCommand):
    help = 'Siembra pacientes de prueba en una clínica (repetible y reversible).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clinic',
            help=(
                'clinic_id de la clínica destino. Se puede omitir si solo hay '
                'una clínica en la base.'
            ),
        )
        parser.add_argument(
            '--count', type=int, default=25,
            help='Cuántos pacientes crear (por defecto 25).',
        )
        parser.add_argument(
            '--seed', type=int, default=0,
            help='Semilla del generador: la misma semilla da el mismo lote.',
        )
        parser.add_argument(
            '--spread-days', type=int, default=540,
            help=(
                'Días hacia atrás entre los que repartir las altas (por defecto '
                '540). 0 los da todos de alta hoy.'
            ),
        )
        parser.add_argument(
            '--email-domain', default='seed.local',
            help=(
                'Dominio de los correos del lote; es la marca que reconoce '
                '--undo (por defecto seed.local).'
            ),
        )
        parser.add_argument(
            '--no-notes', action='store_true',
            help='No rellenar el campo de notas.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Enseña lo que crearía y no escribe nada.',
        )
        parser.add_argument(
            '--undo', action='store_true',
            help='Borra el lote sembrado en esa clínica y no crea nada.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help=(
                'Con --undo, borra también los pacientes que ya tengan '
                'episodios clínicos o facturas colgando.'
            ),
        )

    def handle(self, *args, **options):
        clinic = self._resolve_clinic(options['clinic'])
        domain = options['email_domain'].lstrip('@')

        # El comando firma sus escrituras: el ChangeLog dirá que esto lo hizo
        # `seed_patients` y no un usuario sin nombre.
        with audit_context(origin=ORIGIN_COMMAND, user_repr='comando seed_patients'):
            if options['undo']:
                self._undo(clinic, domain, force=options['force'])
            else:
                self._seed(clinic, domain, options)

    # ------------------------------------------------------------------
    # Clínica
    # ------------------------------------------------------------------

    def _resolve_clinic(self, clinic_id):
        if clinic_id:
            try:
                return Clinic.objects.get(pk=clinic_id)
            except Clinic.DoesNotExist:
                known = ', '.join(Clinic.objects.values_list('pk', flat=True)) or '(ninguna)'
                raise CommandError(
                    f'No existe la clínica «{clinic_id}». Las que hay: {known}.'
                )

        clinics = list(Clinic.objects.all()[:2])
        if not clinics:
            raise CommandError('No hay ninguna clínica en la base; crea una antes.')
        if len(clinics) > 1:
            known = ', '.join(Clinic.objects.values_list('pk', flat=True))
            raise CommandError(
                f'Hay más de una clínica: indica cuál con --clinic. Las que hay: {known}.'
            )
        return clinics[0]

    # ------------------------------------------------------------------
    # Sembrado
    # ------------------------------------------------------------------

    def _seed(self, clinic, domain, options):
        count = options['count']
        if count < 1:
            raise CommandError('--count tiene que ser al menos 1.')
        if count > MAX_PATIENTS:
            raise CommandError(
                f'--count no puede pasar de {MAX_PATIENTS}: es lo que da de sí el '
                f'bloque de teléfonos reservado ({PHONE_PREFIX}XXXX).'
            )

        rng = random.Random(options['seed'])
        rows = self._build_rows(
            rng, count, clinic, domain,
            with_notes=not options['no_notes'],
            spread_days=max(options['spread_days'], 0),
        )

        if options['dry_run']:
            self._preview(rows, clinic)
            return

        created = failed = 0
        for row in rows:
            try:
                # Fila a fila y en su propia transacción: un choque de unicidad
                # no puede llevarse por delante lo ya sembrado.
                with transaction.atomic():
                    patient = create_patient(
                        clinic=clinic,
                        phone=row['phone'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        email=row['email'],
                        date_of_birth=row['date_of_birth'],
                        notes=row['notes'],
                    )
                    if row['created_at'] is not None:
                        # `created_at` es auto_now_add: solo se puede mover
                        # escribiéndolo después. Sin `updated_at` en
                        # update_fields, la marca de modificación no se toca.
                        patient.created_at = row['created_at']
                        patient.save(update_fields=['created_at'])
            except (IntegrityError, ValueError) as exc:
                failed += 1
                self.stderr.write(f'{row["email"]}: {exc}')
                continue
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f'{created} pacientes creados en «{clinic.name}» '
            f'(marca: @{domain}){f", {failed} fallidos" if failed else ""}.'
        ))
        self.stdout.write(
            f'Para deshacerlo: python manage.py seed_patients '
            f'--clinic {clinic.pk} --email-domain {domain} --undo'
        )

    def _build_rows(self, rng, count, clinic, domain, *, with_notes, spread_days):
        """Arma el lote entero en memoria antes de escribir nada.

        Así el `--dry-run` enseña exactamente lo que se va a crear, y los
        teléfonos y correos ya vienen libres de choques con lo que hay.
        """
        taken_phones = set(
            Patient.objects.filter(clinic=clinic, phone__startswith=PHONE_PREFIX)
            .values_list('phone', flat=True)
        )
        taken_emails = set(
            Patient.objects.filter(clinic=clinic, email__endswith=f'@{domain}')
            .values_list('email', flat=True)
        )

        today = timezone.localdate()

        phone_counter = 0
        used_names = set()
        rows = []
        for _ in range(count):
            first_name, last_name = self._pick_name(rng, used_names)

            phone = None
            while phone_counter < MAX_PATIENTS:
                candidate = f'{PHONE_PREFIX}{phone_counter:0{PHONE_DIGITS}d}'
                phone_counter += 1
                if candidate not in taken_phones:
                    phone = candidate
                    break
            if phone is None:
                self.stderr.write(
                    'Se agotó el bloque de teléfonos reservado; se siembra menos '
                    'de lo pedido.'
                )
                break
            taken_phones.add(phone)

            email = self._pick_email(first_name, last_name, domain, taken_emails)
            taken_emails.add(email)

            rows.append({
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'phone': phone,
                'date_of_birth': self._birth_date(rng, today),
                'notes': (
                    rng.choice(NOTES) if with_notes and rng.random() < 0.35 else ''
                ),
                'created_at': self._joined_at(rng, today, spread_days),
            })
        return rows

    def _pick_name(self, rng, used):
        """Nombre y dos apellidos, evitando repetir el mismo par en el lote."""
        for _ in range(50):
            pool = FEMALE_NAMES if rng.random() < 0.55 else MALE_NAMES
            first = rng.choice(pool)
            last = f'{rng.choice(SURNAMES)} {rng.choice(SURNAMES)}'
            if (first, last) not in used:
                used.add((first, last))
                return first, last
        return first, last

    def _pick_email(self, first_name, last_name, domain, taken):
        base = f'{_slug(first_name)}.{_slug(last_name.split()[0])}'
        candidate = f'{base}@{domain}'
        suffix = 2
        while candidate in taken:
            candidate = f'{base}{suffix}@{domain}'
            suffix += 1
        return candidate

    def _joined_at(self, rng, today, spread_days):
        """Fecha de alta repartida hacia atrás, en horario de consulta.

        Con `--spread-days 0` todas las altas son de hoy, y `None` deja el
        `auto_now_add` en paz en vez de reescribirlo con el mismo valor.
        """
        if spread_days <= 0:
            return None
        day = today - timedelta(days=rng.randint(0, spread_days))
        moment = datetime.combine(day, time(rng.randint(9, 19), rng.choice([0, 15, 30, 45])))
        return timezone.make_aware(moment, timezone.get_current_timezone())

    def _birth_date(self, rng, today):
        bands = [(lo, hi) for lo, hi, _ in AGE_BANDS]
        weights = [w for _, _, w in AGE_BANDS]
        low, high = rng.choices(bands, weights=weights)[0]
        age = rng.randint(low, high)
        return today - timedelta(days=int(age * 365.25) + rng.randint(0, 364))

    def _preview(self, rows, clinic):
        self.stdout.write(
            f'{len(rows)} pacientes se crearían en «{clinic.name}» (nada escrito):'
        )
        for row in rows[:10]:
            self.stdout.write(
                f'  {row["first_name"]} {row["last_name"]} · {row["phone"]} · '
                f'{row["email"]} · nac. {row["date_of_birth"]} · '
                f'alta {row["created_at"].date() if row["created_at"] else "hoy"}'
            )
        if len(rows) > 10:
            self.stdout.write(f'  … y {len(rows) - 10} más.')

    # ------------------------------------------------------------------
    # Deshacer
    # ------------------------------------------------------------------

    def _undo(self, clinic, domain, *, force):
        """Borra los pacientes del lote, uno a uno y con las señales puestas.

        Se salta los que ya tengan actividad clínica o facturas, salvo
        `--force`: esos datos sobreviven al borrado del paciente a propósito
        (`DO_NOTHING`), y dejarlos huérfanos por limpiar datos de prueba no
        compensa.

        La historia clínica en sí no cuenta como actividad: `clinical` abre una
        con cada alta (`clinical/signals.py`), así que todo paciente tiene la
        suya desde el primer segundo. Lo que pesa es lo que cuelga de ella. Esa
        historia vacía se queda donde está tras el borrado —una historia nunca
        se borra— y es el escenario huérfano que el propio modelo describe.
        """
        from billing.models import PatientInvoice
        from clinical.models import Episode

        queryset = Patient.objects.filter(clinic=clinic, email__endswith=f'@{domain}')
        total = queryset.count()
        if not total:
            self.stdout.write(f'No hay pacientes con la marca @{domain} en «{clinic.name}».')
            return

        deleted = skipped = 0
        for patient in queryset.iterator():
            if not force:
                has_activity = (
                    Episode.all_objects.filter(history__patient_id=patient.pk).exists()
                    or PatientInvoice.all_objects.filter(patient_id=patient.pk).exists()
                )
                if has_activity:
                    skipped += 1
                    self.stderr.write(
                        f'{patient} (#{patient.pk}) tiene episodios o facturas; '
                        'se deja. Usa --force para borrarlo igualmente.'
                    )
                    continue
            # Instancia a instancia: el `delete()` de queryset puede irse por el
            # camino rápido y saltarse las señales que escriben el ChangeLog.
            patient.delete()
            deleted += 1

        self.stdout.write(self.style.SUCCESS(
            f'{deleted} pacientes borrados de «{clinic.name}»'
            f'{f", {skipped} conservados" if skipped else ""}.'
        ))
