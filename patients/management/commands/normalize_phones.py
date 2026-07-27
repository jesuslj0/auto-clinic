"""Normaliza a E.164 los teléfonos ya guardados de `Patient`.

Migración de datos puntual: los teléfonos que entran hoy se normalizan en el
serializer y en el formulario, así que esto es para las filas anteriores a esa
validación.

**Por qué guarda con `save()` y no con `queryset.update()`.** `Patient` está
registrado en `audit`, y un `update()` masivo se salta las señales: el teléfono
cambiaría sin dejar ni una línea en el `ChangeLog`. Un dato de contacto de un
paciente que cambia sin rastro es justo lo que la auditoría existe para impedir,
y da igual que lo cambie una persona o un comando. Por eso se recorre fila a
fila, pagando el coste, y el evento queda atribuido al comando
(`origin='command'`).

**`Clinic.whatsapp_phone_number_id` NO se toca**, aunque parezca un teléfono y
una versión anterior de este comando lo normalizara. No es un número: es el
*Phone Number ID* que asigna Meta, y se usa tal cual como segmento de la URL de
la Graph API (`agent/whatsapp.py`) y como clave para localizar la clínica desde
el webhook (`AgentConfigByPhoneView`). Pasarlo a E.164 rompe las dos cosas.
"""
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from audit.context import ORIGIN_COMMAND, audit_context
from patients.models import Patient
from patients.services import normalize_phone_safe


class Command(BaseCommand):
    help = 'Normaliza los teléfonos de Patient al formato E.164.'

    def handle(self, *args, **options):
        # Todo lo que se toque aquí queda en el ChangeLog como acción del
        # comando, no de un usuario fantasma.
        with audit_context(origin=ORIGIN_COMMAND, user_repr='comando normalize_phones'):
            self._normalize(Patient, 'phone', 'Patient')

    def _normalize(self, model, field, label):
        # Se resuelve primero qué hay que cambiar y se escribe después: mezclar
        # lecturas en cursor con escrituras sobre la misma conexión es pedir
        # problemas, y así el recuento es firme antes de tocar nada.
        pending = []
        for instance in model.objects.iterator():
            current = getattr(instance, field)
            if not current:
                continue
            normalized = normalize_phone_safe(current)
            if normalized is None:
                pending.append((instance, None))
                continue
            if normalized != current:
                pending.append((instance, normalized))

        update_fields = [field]
        if any(f.name == 'updated_at' for f in model._meta.fields):
            # Con `update_fields`, Django solo escribe los campos nombrados: si
            # `updated_at` no entra, la fila cambia y su marca de tiempo no.
            update_fields.append('updated_at')

        updated = skipped = conflicts = 0
        for instance, normalized in pending:
            if normalized is None:
                skipped += 1
                continue

            previous = getattr(instance, field)
            setattr(instance, field, normalized)
            try:
                # Cada fila en su transacción: un choque de unicidad no puede
                # llevarse por delante lo ya normalizado.
                with transaction.atomic():
                    instance.save(update_fields=update_fields)
            except IntegrityError:
                conflicts += 1
                self.stderr.write(
                    f'{label} #{instance.pk}: {previous} → {normalized} choca con '
                    f'otra fila existente; se deja como estaba.'
                )
                continue
            updated += 1

        self.stdout.write(
            f'{label} — actualizados: {updated}, omitidos: {skipped}, '
            f'conflictos: {conflicts}'
        )
