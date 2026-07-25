"""Purga los registros de auditoría que han superado su plazo de retención.

Esta es la única vía legítima para borrar auditoría. Necesita cooperar con las
dos capas de inmutabilidad:

- El ORM prohíbe `queryset.delete()` sobre estos modelos, así que el borrado se
  hace en SQL crudo.
- El trigger de PostgreSQL rechaza cualquier DELETE salvo que la transacción
  traiga puesta la válvula `audit.purga = 'on'`. La ponemos con `SET LOCAL`, que
  muere al cerrar la transacción: la ventana de borrado es exactamente la de
  este comando y ni un statement más.

El «qué se puede borrar» (fecha de corte por clínica y por tipo de log) se
decide aquí, en Python, sobre el queryset. El trigger no sabe nada de plazos.

Cada purga deja su propio registro (un `ChangeLog` de tipo baja) con el número
de filas eliminadas y su rango temporal, para que un hueco en la línea temporal
de la auditoría nunca sea silencioso.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, models, transaction
from django.utils import timezone

from audit.conf import (
    RETENTION_ACCESS,
    RETENTION_CHANGE,
    retention_days,
    retention_days_by_clinic,
)
from audit.context import ORIGIN_COMMAND
from audit.models import AccessLog, ChangeLog

# (modelo, clave de retención). El orden es estable para que la salida y los
# meta-logs sean deterministas.
LOG_MODELS = [
    (ChangeLog, RETENTION_CHANGE),
    (AccessLog, RETENTION_ACCESS),
]


class Command(BaseCommand):
    help = (
        'Borra los registros de auditoría más antiguos que su plazo de '
        'retención. Deja un meta-log de cada purga. Con --dry-run no borra '
        'nada, solo informa de qué se borraría.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No borra nada; solo cuenta e informa de lo que se purgaría.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()
        days = retention_days()
        by_clinic = retention_days_by_clinic()

        # 1) Planificación (solo lecturas): qué filas caen fuera de plazo.
        plan = []
        for model, type_key in LOG_MODELS:
            pks = self._eligible_pks(model, type_key, now, days[type_key], by_clinic)
            span = self._time_span(model, pks)
            plan.append((model, type_key, pks, span, days[type_key]))

        total = sum(len(pks) for _, _, pks, _, _ in plan)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se borra nada.'))
            for model, type_key, pks, span, days_kept in plan:
                self._report(model, pks, span, days_kept, verb='se purgarían')
            self.stdout.write(f'Total que se purgaría: {total} filas.')
            return

        if total == 0:
            self.stdout.write('Nada que purgar: no hay registros fuera de plazo.')
            return

        # 2) Borrado real, dentro de una única transacción con la válvula puesta.
        with transaction.atomic():
            with connection.cursor() as cursor:
                # SET LOCAL: la válvula solo vive hasta el fin de ESTA
                # transacción. Fuera de aquí, cualquier DELETE vuelve a estar
                # bloqueado por el trigger.
                cursor.execute("SET LOCAL audit.purga = 'on'")

                for model, type_key, pks, span, days_kept in plan:
                    if not pks:
                        continue
                    table = model._meta.db_table
                    cursor.execute(
                        f'DELETE FROM {table} WHERE id = ANY(%s)', [list(pks)]
                    )
                    self._report(model, pks, span, days_kept, verb='purgadas')
                    # Meta-log DENTRO de la misma transacción: si el borrado se
                    # revierte, su registro se revierte con él. INSERT no lo
                    # frena el trigger, que solo mira UPDATE/DELETE.
                    self._write_meta_log(model, len(pks), span, days_kept, now)

        self.stdout.write(self.style.SUCCESS(f'Purga completada: {total} filas.'))

    # ------------------------------------------------------------------
    # Selección de filas fuera de plazo
    # ------------------------------------------------------------------

    def _eligible_pks(self, model, type_key, now, default_days, by_clinic):
        """PKs cuyo `timestamp` ha superado el plazo de retención aplicable.

        Cada clínica con override se juzga por su propio plazo (resuelto a
        través de `patient.clinic`). Todo lo demás —incluidas las filas sin
        paciente— cae bajo el plazo por defecto del tipo.
        """
        override_clinic_ids = list(by_clinic.keys())
        pks = set()

        for clinic_id, cfg in by_clinic.items():
            cutoff = now - timedelta(days=cfg.get(type_key, default_days))
            pks.update(
                model.objects.filter(
                    patient__clinic_id=clinic_id, timestamp__lt=cutoff
                ).values_list('pk', flat=True)
            )

        default_cutoff = now - timedelta(days=default_days)
        default_qs = model.objects.filter(timestamp__lt=default_cutoff)
        if override_clinic_ids:
            # Las filas de una clínica con override ya se han juzgado con su
            # propio plazo; no se re-evalúan con el plazo por defecto.
            default_qs = default_qs.exclude(patient__clinic_id__in=override_clinic_ids)
        pks.update(default_qs.values_list('pk', flat=True))

        return pks

    def _time_span(self, model, pks):
        """Rango temporal (más antiguo, más reciente) de las filas a purgar."""
        if not pks:
            return None, None
        agg = model.objects.filter(pk__in=pks).aggregate(
            oldest=models.Min('timestamp'), newest=models.Max('timestamp')
        )
        return agg['oldest'], agg['newest']

    # ------------------------------------------------------------------
    # Salida y meta-log
    # ------------------------------------------------------------------

    def _report(self, model, pks, span, days_kept, verb):
        oldest, newest = span
        if not pks:
            self.stdout.write(
                f'{model.__name__}: 0 filas {verb} (retención {days_kept} días).'
            )
            return
        self.stdout.write(
            f'{model.__name__}: {len(pks)} filas {verb} '
            f'(retención {days_kept} días; rango {_fmt(oldest)} … {_fmt(newest)}).'
        )

    def _write_meta_log(self, model, count, span, days_kept, now):
        oldest, newest = span
        cutoff = now - timedelta(days=days_kept)
        ChangeLog.objects.create(
            action=ChangeLog.Action.DELETE,
            model_label=model._meta.label,
            object_repr=(
                f'Purga de {count} {model._meta.verbose_name_plural} '
                f'anteriores a {_fmt(cutoff)}'
            ),
            origin=ORIGIN_COMMAND,
            user_repr='comando purge_audit_logs',
            changes={
                'purged_count': count,
                'retention_days': days_kept,
                'cutoff': cutoff.isoformat(),
                'oldest_purged': oldest.isoformat() if oldest else None,
                'newest_purged': newest.isoformat() if newest else None,
            },
        )


def _fmt(value):
    return value.strftime('%Y-%m-%d %H:%M') if value else '—'
