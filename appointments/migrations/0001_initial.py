import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('core', '0001_initial'),
        ('patients', '0001_initial'),
        ('services', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Appointment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('scheduled_at', models.DateTimeField()),
                ('end_at', models.DateTimeField()),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled'), ('no_show', 'No Show')], default='pending', max_length=20)),
                ('confirmation_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('notes', models.TextField(blank=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_appointments', to='core.user')),
                ('clinic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='appointments', to='core.clinic')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='appointments', to='patients.patient')),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='appointments', to='services.service')),
            ],
            options={'ordering': ['scheduled_at']},
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['clinic', 'scheduled_at', 'status'], name='appointmen_clinic__fe1c70_idx'),
        ),
    ]
