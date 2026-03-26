import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClinicKnowledgeBase',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kb_type', models.CharField(choices=[('pricing', 'Pricing'), ('schedule', 'Schedule'), ('faq', 'FAQ'), ('location', 'Location'), ('policies', 'Policies'), ('team', 'Team')], max_length=50)),
                ('title', models.CharField(blank=True, max_length=200)),
                ('content', models.TextField()),
                ('metadata', models.JSONField(default=dict)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('clinic', models.ForeignKey(db_column='clinic_id', on_delete=django.db.models.deletion.CASCADE, related_name='knowledge_base', to='core.clinic')),
            ],
            options={
                'db_table': 'clinic_knowledge_base',
            },
        ),
        migrations.CreateModel(
            name='ClinicInfoQuery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('question', models.TextField()),
                ('intent_category', models.CharField(blank=True, max_length=50)),
                ('answer', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('clinic', models.ForeignKey(blank=True, db_column='clinic_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='info_queries', to='core.clinic')),
            ],
            options={
                'db_table': 'clinic_info_queries',
            },
        ),
        migrations.CreateModel(
            name='ClinicInfoCache',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('normalized_question', models.TextField()),
                ('answer', models.TextField(blank=True)),
                ('intent_category', models.CharField(blank=True, max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('clinic', models.ForeignKey(db_column='clinic_id', on_delete=django.db.models.deletion.CASCADE, related_name='info_cache', to='core.clinic')),
            ],
            options={
                'db_table': 'clinic_info_cache',
            },
        ),
        migrations.AddIndex(
            model_name='clinicknowledgebase',
            index=models.Index(fields=['clinic', 'active', 'kb_type'], name='idx_kb_clinic_active_type'),
        ),
        migrations.AddConstraint(
            model_name='clinicinfocache',
            constraint=models.UniqueConstraint(fields=['clinic', 'normalized_question'], name='uq_cache_clinic_question'),
        ),
    ]
