from django.core.management.base import BaseCommand

from core.models import Clinic
from patients.models import Patient
from patients.services import normalize_phone_safe


class Command(BaseCommand):
    help = "Normaliza todos los teléfonos de Patient y Clinic al formato E.164."

    def handle(self, *args, **options):
        updated = 0
        skipped = 0

        for patient in Patient.objects.iterator():
            normalized = normalize_phone_safe(patient.phone)
            if normalized is None:
                skipped += 1
                continue
            if normalized != patient.phone:
                Patient.objects.filter(pk=patient.pk).update(phone=normalized)
                updated += 1

        self.stdout.write(f"Patient — actualizados: {updated}, omitidos: {skipped}")

        updated_c = 0
        skipped_c = 0

        for clinic in Clinic.objects.iterator():
            phone = clinic.whatsapp_phone_number_id
            if not phone:
                continue
            normalized = normalize_phone_safe(phone)
            if normalized is None:
                skipped_c += 1
                continue
            if normalized != phone:
                Clinic.objects.filter(pk=clinic.pk).update(
                    whatsapp_phone_number_id=normalized
                )
                updated_c += 1

        self.stdout.write(
            f"Clinic (whatsapp_phone_number_id) — actualizados: {updated_c}, omitidos: {skipped_c}"
        )
