import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        ('patients', '0001_initial'),
        ('services', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Appointment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('patient_phone', models.CharField(blank=True, max_length=20)),
                ('patient_name', models.CharField(blank=True, max_length=255)),
                ('service_name', models.CharField(blank=True, max_length=255)),
                ('scheduled_at', models.DateTimeField()),
                ('end_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('confirmed', 'Confirmed'),
                        ('cancelled', 'Cancelled'),
                        ('rescheduled', 'Rescheduled'),
                        ('no_show', 'No Show'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('confirmation_token', models.UUIDField(default=uuid.uuid4, unique=True)),
                ('external_id', models.CharField(blank=True, max_length=255)),
                ('external_calendar_id', models.CharField(blank=True, max_length=255)),
                ('notes', models.TextField(blank=True)),
                ('cancellation_policy_hours', models.IntegerField(default=24)),
                ('reminder_24h_sent', models.BooleanField(default=False)),
                ('reminder_24h_sent_at', models.DateTimeField(blank=True, null=True)),
                ('reminder_responded', models.BooleanField(default=False)),
                ('reminder_3h_sent', models.BooleanField(default=False)),
                ('reminder_3h_sent_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('clinic', models.ForeignKey(
                    db_column='clinic_id',
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='appointments',
                    to='core.clinic',
                )),
                ('patient', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='appointments',
                    to='patients.patient',
                )),
                ('service', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='appointments',
                    to='services.service',
                )),
                ('assigned_to', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='appointments',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'appointments',
                'ordering': ['scheduled_at'],
            },
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['clinic', 'scheduled_at', 'status'], name='idx_appointments_clinic_dt_status'),
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['patient_phone', 'status', 'scheduled_at'], name='idx_appointments_phone_status_dt'),
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['reminder_24h_sent', 'reminder_responded', 'reminder_3h_sent'], name='idx_appointments_reminder_flags'),
        ),
    ]
