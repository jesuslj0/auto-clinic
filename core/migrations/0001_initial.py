import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import core.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='Clinic',
            fields=[
                ('clinic_id', models.CharField(max_length=100, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('timezone', models.CharField(default='Europe/Madrid', max_length=50)),
                ('whatsapp_phone_number_id', models.CharField(blank=True, max_length=100)),
                ('api_type', models.CharField(
                    blank=True,
                    choices=[
                        ('calendly', 'Calendly'),
                        ('google_calendar', 'Google Calendar'),
                        ('custom', 'Custom'),
                    ],
                    max_length=20,
                )),
                ('api_url', models.CharField(blank=True, max_length=500)),
                ('api_key', models.CharField(blank=True, max_length=500)),
                ('calendly_link', models.CharField(blank=True, max_length=500)),
                ('calendly_token', models.CharField(blank=True, max_length=500)),
                ('calendly_event_type_uuid', models.UUIDField(blank=True, null=True)),
                ('google_calendar_id', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'db_table': 'clinics',
            },
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(
                    default=False,
                    help_text='Designates that this user has all permissions without explicitly assigning them.',
                    verbose_name='superuser status',
                )),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('is_staff', models.BooleanField(
                    default=False,
                    help_text='Designates whether the user can log into this admin site.',
                    verbose_name='staff status',
                )),
                ('is_active', models.BooleanField(
                    default=True,
                    help_text='Designates whether this user should be treated as active.',
                    verbose_name='active',
                )),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('username', models.CharField(blank=True, max_length=150, unique=True)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('role', models.CharField(
                    choices=[('admin', 'Admin'), ('staff', 'Staff')],
                    default='staff',
                    max_length=20,
                )),
                ('clinic', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='users',
                    to='core.clinic',
                )),
                ('groups', models.ManyToManyField(
                    blank=True,
                    related_name='user_set',
                    related_query_name='user',
                    to='auth.group',
                    verbose_name='groups',
                )),
                ('user_permissions', models.ManyToManyField(
                    blank=True,
                    related_name='user_set',
                    related_query_name='user',
                    to='auth.permission',
                    verbose_name='user permissions',
                )),
            ],
            options={
                'ordering': ['email'],
            },
            managers=[
                ('objects', core.models.UserManager()),
            ],
        ),
    ]
