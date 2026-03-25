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
            name='AgentMemory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('session_id', models.CharField(max_length=100)),
                ('messages', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_memory',
            },
        ),
        migrations.CreateModel(
            name='WorkflowError',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('workflow', models.CharField(blank=True, max_length=100)),
                ('workflow_name', models.CharField(blank=True, max_length=100)),
                ('node_name', models.CharField(blank=True, max_length=100)),
                ('error_message', models.TextField()),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('payload', models.JSONField(blank=True, null=True)),
                ('input_data', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'workflow_errors',
            },
        ),
        migrations.CreateModel(
            name='ConversationSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('phone', models.CharField(max_length=20, unique=True)),
                ('session_data', models.JSONField(default=dict)),
                ('last_interaction', models.DateTimeField(blank=True, null=True)),
                ('appointment_context', models.JSONField(default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('clinic', models.ForeignKey(blank=True, db_column='clinic_id', null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.clinic')),
            ],
            options={
                'db_table': 'conversation_sessions',
            },
        ),
        migrations.AddIndex(
            model_name='agentmemory',
            index=models.Index(fields=['session_id'], name='idx_agent_memory_session'),
        ),
        migrations.AddIndex(
            model_name='workflowerror',
            index=models.Index(fields=['workflow', 'created_at'], name='idx_workflow_errors_workflow_dt'),
        ),
    ]
