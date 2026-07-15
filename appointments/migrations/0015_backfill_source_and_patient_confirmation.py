from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    """Rellena `source` y `patient_confirmed_at` en las citas que ya existían.

    - `patient_confirmed_at`: las citas confirmadas con `reminder_responded=True`
      se confirmaron porque el paciente respondió al recordatorio (era la única
      vía que escribía ese flag). Recuperamos el momento aproximado con
      `updated_at`, que es lo mejor que tenemos.
    - `source`: las citas del bot llegan sin `Patient` asociado pero con el
      teléfono denormalizado. Lo demás se queda en el default (`staff`).
    - `hold_expires_at`: se queda a NULL. Las citas ya creadas NO caducan; nadie
      les prometió un plazo y no vamos a cancelarlas retroactivamente.

    `status` no se toca.
    """
    Appointment = apps.get_model('appointments', 'Appointment')

    Appointment.objects.filter(
        status='confirmed',
        reminder_responded=True,
        patient_confirmed_at__isnull=True,
    ).update(patient_confirmed_at=F('updated_at'))

    Appointment.objects.filter(patient__isnull=True).exclude(patient_phone='').update(
        source='agent'
    )


def unbackfill(apps, schema_editor):
    Appointment = apps.get_model('appointments', 'Appointment')
    Appointment.objects.update(patient_confirmed_at=None, source='staff')


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0014_appointment_hold_expires_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
