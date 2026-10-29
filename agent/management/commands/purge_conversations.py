"""Borra los mensajes de los hilos de WhatsApp inactivos más allá del plazo de la clínica.

La lógica vive en `agent.retention` y es la misma que lanza la tarea nocturna de Celery
(`agent.tasks.purge_old_conversations`); esto es para probarla con `--dry-run` o lanzarla a mano.
"""
from django.core.management.base import BaseCommand

from agent.retention import purge_conversations


class Command(BaseCommand):
    help = (
        'Borra los mensajes de las conversaciones sin actividad desde hace más de '
        '`conversation_retention_months` de su clínica (las fotos se conservan). '
        'Con --dry-run solo informa de lo que se borraría.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='No borra nada; solo informa.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        report = purge_conversations(dry_run=dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se borra nada.'))
        verb = 'se borrarían' if dry_run else 'borrados'
        for clinic_id, r in report.by_clinic.items():
            self.stdout.write(
                f'{clinic_id or "(sin clínica)"}: {r.messages} mensajes {verb} en {r.sessions} conversaciones '
                f'(plazo {r.months} meses; {r.audio_files} audios, {r.photos_kept} fotos conservadas).'
            )
        if not report.by_clinic:
            self.stdout.write('Nada que purgar: ningún hilo supera su plazo.')
        else:
            self.stdout.write(self.style.SUCCESS(f'Total: {report.messages} mensajes.'))
