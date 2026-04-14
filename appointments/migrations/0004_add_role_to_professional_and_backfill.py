from django.db import migrations, models


def create_missing_professionals(apps, schema_editor):
    User = apps.get_model('core', 'User')
    Professional = apps.get_model('appointments', 'Professional')

    for user in User.objects.exclude(role='').exclude(clinic__isnull=True):
        Professional.objects.get_or_create(
            user=user,
            defaults={
                'clinic': user.clinic,
                'role': user.role,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0003_remove_appointment_assigned_to_professional_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='professional',
            name='role',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(create_missing_professionals, migrations.RunPython.noop),
    ]
