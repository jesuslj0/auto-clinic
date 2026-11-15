from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0016_whatsapptemplate_booking_notice_purposes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentprofile',
            name='welcome_message',
            field=models.TextField(
                blank=True,
                help_text='El agente abre con este texto, tal cual, su primer mensaje a alguien '
                          'que no es paciente todavía, seguido del aviso de que es una IA.',
                max_length=500,
                verbose_name='presentación para contactos nuevos',
            ),
        ),
    ]
