from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_clinic_whatsapp_business_account_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='clinic',
            name='privacy_policy_url',
            field=models.URLField(blank=True, help_text='Enlace público a la política de privacidad de la clínica. El agente de WhatsApp lo da en su primer mensaje. Vacío: avisa igualmente, sin enlace.', verbose_name='política de privacidad'),
        ),
    ]
