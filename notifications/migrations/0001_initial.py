from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('appointments', '0001_initial'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reminder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reminder_type', models.CharField(choices=[('24h', '24 Hours'), ('2h', '2 Hours')], max_length=10)),
                ('scheduled_for', models.DateTimeField()),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('success', models.BooleanField(default=False)),
                ('appointment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='appointments.appointment')),
                ('clinic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='core.clinic')),
            ],
            options={'ordering': ['scheduled_for'], 'unique_together': {('appointment', 'reminder_type')}},
        ),
    ]
