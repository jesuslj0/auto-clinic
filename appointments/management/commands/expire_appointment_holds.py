"""Cancela las citas cuyo hold ha caducado y libera el hueco.

Contrapartida imprescindible de que `pending` bloquee: si una cita reservada por
el agente ocupa el hueco desde el minuto uno, alguien tiene que soltarlo cuando
la clínica no la valida. Sin este comando, una reserva que nadie mira se come el
hueco para siempre.

Cómo programarlo. El proyecto ya trae `CELERY_BEAT_SCHEDULE`
(config/settings/base.py) para los recordatorios, así que lo natural es añadir
ahí una entrada que llame a `appointments.tasks.expire_appointment_holds`:

    'expire-appointment-holds': {
        'task': 'appointments.tasks.expire_appointment_holds',
        'schedule': 600.0,   # cada 10 minutos
    },

OJO: hoy `docker-compose.yml` solo levanta un worker (`celery -A config worker`),
NO hay servicio `beat`, así que ese schedule no se ejecuta en local. Mientras no
lo haya, esto se lanza a mano o por cron:

    python manage.py expire_appointment_holds
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment, AppointmentStatusHistory
from appointments.services import cancel_appointment


class Command(BaseCommand):
    help = 'Cancela las citas cuyo hold ha caducado y libera el hueco.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Enumera las citas que caducarían, sin tocarlas.',
        )

    def handle(self, *args, **options):
        ahora = timezone.now()

        # Solo caducan las que siguen esperando validación. Una cita que el staff
        # ya puso en firme no tiene `hold_expires_at` (lo borra `confirm_by_clinic`),
        # pero el filtro por estado lo deja explícito igualmente: `confirmed` NUNCA
        # caduca, aunque arrastrara un hold de antes.
        caducadas = Appointment.objects.filter(
            status=Appointment.Status.PENDING,
            hold_expires_at__isnull=False,
            hold_expires_at__lt=ahora,
        ).select_related('clinic')

        if options['dry_run']:
            for cita in caducadas:
                self.stdout.write(f'[dry-run] caducaría: {cita.pk} — {cita}')
            self.stdout.write(self.style.WARNING(f'{caducadas.count()} citas caducarían.'))
            return 0

        total = 0
        for cita in caducadas:
            # Por el service, con su guardia y su historial. Nunca `.update()`: eso
            # cancelaría en masa sin dejar rastro de quién ni por qué.
            cancel_appointment(
                cita,
                actor=AppointmentStatusHistory.Actor.SYSTEM,
                actor_label='Hold caducado',
            )
            total += 1

        # Idempotente: en la segunda pasada ya no queda ninguna `pending` caducada,
        # porque las de la primera están `cancelled`.
        self.stdout.write(self.style.SUCCESS(f'{total} citas caducadas y liberadas.'))
        return 0
