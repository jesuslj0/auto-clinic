"""Siembra las categorías de podología en las clínicas que ya existen.

Las clínicas nuevas las reciben de la señal de `services.signals`; aquí se
cubren las anteriores. Se salta cualquier nombre que la clínica ya tenga, así
que se puede reaplicar sin duplicar nada.

La lista va copiada y no importada de `services.models`: una migración describe
un momento del historial, y cambiar mañana las categorías por defecto no debe
reescribir lo que se sembró hoy.
"""
from django.db import migrations

SEED = (
    ('Quiropodia', '#7c3aed'),
    ('Onicología', '#e879a6'),
    ('Biomecánica', '#0ea5e9'),
    ('Ortopodología', '#14b8a6'),
    ('Cirugía ungueal', '#f97316'),
    ('Pie diabético', '#ef4444'),
    ('Podología deportiva', '#84cc16'),
    ('Podología infantil', '#eab308'),
)


def seed_categories(apps, schema_editor):
    Clinic = apps.get_model('core', 'Clinic')
    ServiceCategory = apps.get_model('services', 'ServiceCategory')

    for clinic in Clinic.objects.all():
        existentes = set(
            ServiceCategory.objects.filter(clinic=clinic).values_list('name', flat=True)
        )
        ServiceCategory.objects.bulk_create(
            [
                ServiceCategory(clinic=clinic, name=nombre, color=color)
                for nombre, color in SEED
                if nombre not in existentes
            ]
        )


def unseed_categories(apps, schema_editor):
    """Solo retira las semillas que nadie llegó a usar."""
    ServiceCategory = apps.get_model('services', 'ServiceCategory')
    ServiceCategory.objects.filter(
        name__in=[nombre for nombre, _ in SEED],
        services__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0003_servicecategory_service_category'),
        ('core', '0008_clinic_agent_enabled_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
