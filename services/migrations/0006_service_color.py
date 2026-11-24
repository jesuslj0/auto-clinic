import django.core.validators
from django.db import migrations, models

# Copia de `services.models.SERVICE_PALETTE` al escribir la migración: una migración
# no debe depender de constantes que luego puedan cambiar.
PALETTE = [
    '#7c3aed', '#0ea5e9', '#14b8a6', '#f97316', '#e11d48', '#84cc16',
    '#d946ef', '#2563eb', '#eab308', '#059669', '#ef4444', '#64748b',
]


def assign_colors(apps, schema_editor):
    """Da un color a cada servicio existente, repartiendo la paleta por clínica."""
    Service = apps.get_model('services', 'Service')
    contador = {}
    # Un `save()` por servicio y no un `update()` masivo: ver CLAUDE.md sobre bulk.
    for service in Service.objects.order_by('clinic_id', 'name'):
        n = contador.get(service.clinic_id, 0)
        contador[service.clinic_id] = n + 1
        service.color = PALETTE[n % len(PALETTE)]
        service.save(update_fields=['color'])


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0005_alter_service_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='color',
            field=models.CharField(
                blank=True, max_length=7,
                help_text='Color hexadecimal con el que se ve el servicio en la agenda.',
                validators=[django.core.validators.RegexValidator('^#[0-9a-fA-F]{6}$', 'Usa un color hexadecimal, p. ej. #7c3aed.')],
            ),
        ),
        migrations.RunPython(assign_colors, migrations.RunPython.noop),
    ]
