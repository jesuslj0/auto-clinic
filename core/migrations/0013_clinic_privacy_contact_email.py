from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_clinic_privacy_policy_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='clinic',
            name='privacy_contact_email',
            field=models.EmailField(blank=True, help_text='Dónde ejercer los derechos RGPD (o correo del delegado de protección de datos, si lo hay). Vacío: se usa el correo general de la clínica.', max_length=254, verbose_name='correo de protección de datos'),
        ),
    ]
