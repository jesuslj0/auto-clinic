"""Retención de chats: los hilos inactivos pierden sus mensajes, no las fotos ni la conversación.

  agent.retention.purge_conversations   — la lógica
  manage.py purge_conversations         — a mano, con --dry-run
  agent.tasks.purge_old_conversations   — la tarea nocturna del beat
"""
from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from agent.models import AgentMemory, ChatAttachment, ChatMessage, ConversationSession
from agent.media import attach_media
from agent.retention import DEFAULT_RETENTION_MONTHS, months_before, purge_conversations
from agent.services import record_message
from agent.tasks import purge_old_conversations
from audit.models import ChangeLog
from tests.agent.test_chat_media import OGG_OPUS, jpeg_with_exif, upload

PHONE = '+34600111222'
SECRET = 'me duele el talón desde el martes'

pytestmark = pytest.mark.django_db


def _storage():
    return ChatAttachment._meta.get_field('file').storage


def months_ago(n):
    return months_before(timezone.now(), n)


def thread(clinic, *, phone=PHONE, last=None, texts=(SECRET, 'ok gracias')):
    """Hilo con mensajes de texto cuyo último mensaje es `last` (por defecto, hace 25 meses)."""
    last = last or months_ago(25)
    session = ConversationSession.objects.create(clinic=clinic, phone=phone)
    for text in texts:
        record_message(
            clinic=clinic, session=session, direction='inbound', sender='patient',
            body=text, sent_at=last,
        )
    ChatMessage.objects.filter(session=session).update(created_at=last)
    ConversationSession.objects.filter(pk=session.pk).update(
        last_message_at=last, unread_count=2, last_message_preview=SECRET,
        session_data={'last_response': SECRET}, appointment_context={'service': 'quiropodia'},
    )
    session.refresh_from_db()
    return session


def add_media(session, kind):
    message = record_message(
        clinic=session.clinic, session=session, direction='inbound', sender='patient',
        body=f'[{kind}]', message_type=kind,
    )
    content = jpeg_with_exif() if kind == 'image' else OGG_OPUS
    return attach_media(message, upload(content, 'x.jpg' if kind == 'image' else 'x.ogg',
                                        'image/jpeg' if kind == 'image' else 'audio/ogg'))


def run(**kwargs):
    return purge_conversations(**kwargs)


class TestMonthsBefore:
    def test_clamps_to_the_end_of_the_month(self):
        assert months_before(datetime(2026, 3, 31, tzinfo=dt_timezone.utc), 1) == datetime(2026, 2, 28, tzinfo=dt_timezone.utc)

    def test_crosses_years(self):
        assert months_before(datetime(2026, 1, 15, tzinfo=dt_timezone.utc), 24) == datetime(2024, 1, 15, tzinfo=dt_timezone.utc)
        assert months_before(datetime(2026, 2, 10, tzinfo=dt_timezone.utc), 3) == datetime(2025, 11, 10, tzinfo=dt_timezone.utc)


class TestWhatGetsPurged:
    def test_inactive_thread_loses_its_messages_but_keeps_the_conversation(self, clinic_a):
        session = thread(clinic_a)

        report = run()

        assert report.messages == 2 and report.sessions == 1
        assert not ChatMessage.objects.filter(session=session).exists()
        session.refresh_from_db()
        assert session.phone == PHONE and session.clinic_id == clinic_a.pk
        assert session.last_message_preview == '' and session.unread_count == 0
        assert session.session_data == {} and session.appointment_context == {}
        assert session.last_message_at is not None

    def test_active_thread_is_untouched_even_with_old_messages(self, clinic_a):
        session = thread(clinic_a, last=months_ago(25))
        # El paciente vuelve a escribir: el hilo ya no está inactivo.
        record_message(clinic=clinic_a, session=session, direction='inbound', sender='patient', body='hola otra vez')

        report = run()

        assert report.messages == 0
        assert ChatMessage.objects.filter(session=session).count() == 3

    def test_thread_inside_the_deadline_is_untouched(self, clinic_a):
        thread(clinic_a, last=months_ago(23))

        assert run().messages == 0
        assert ChatMessage.objects.count() == 2

    def test_each_clinic_uses_its_own_deadline(self, clinic_a, clinic_b):
        clinic_b.conversation_retention_months = 6
        clinic_b.save()
        thread(clinic_a, last=months_ago(8))
        thread(clinic_b, phone='+34600999888', last=months_ago(8))

        run()

        assert ChatMessage.objects.filter(clinic=clinic_a).count() == 2
        assert ChatMessage.objects.filter(clinic=clinic_b).count() == 0

    def test_zero_months_means_no_limit(self, clinic_a):
        clinic_a.conversation_retention_months = 0
        clinic_a.save()
        thread(clinic_a, last=months_ago(120))

        assert run().messages == 0

    def test_a_thread_without_clinic_gets_the_default_deadline(self, clinic_a):
        session = thread(clinic_a)
        ConversationSession.objects.filter(pk=session.pk).update(clinic=None)

        assert run().messages == 2
        assert DEFAULT_RETENTION_MONTHS == 24

    def test_a_thread_that_never_had_messages_is_ignored(self, clinic_a):
        ConversationSession.objects.create(clinic=clinic_a, phone=PHONE)

        assert run().sessions == 0


class TestPhotosAndVoiceNotes:
    def test_photos_stay_with_their_message_and_voice_notes_go(self, clinic_a, django_capture_on_commit_callbacks):
        session = thread(clinic_a)
        photo = add_media(session, 'image')
        voice = add_media(session, 'audio')
        old = months_ago(25)
        ChatMessage.objects.filter(session=session).update(created_at=old)
        ConversationSession.objects.filter(pk=session.pk).update(last_message_at=old)
        voice_key = voice.file.name
        assert _storage().exists(voice_key) and _storage().exists(photo.file.name)

        with django_capture_on_commit_callbacks(execute=True):
            report = run()

        assert report.messages == 3 and report.audio_files == 1
        assert report.by_clinic[clinic_a.pk].photos_kept == 1
        # La foto y su mensaje siguen ahí, con el fichero en el bucket.
        assert list(ChatMessage.objects.filter(session=session)) == [photo.message]
        assert ChatAttachment.objects.filter(pk=photo.pk).exists()
        assert _storage().exists(photo.file.name)
        # La nota de voz se va entera: fila y objeto del bucket.
        assert not ChatAttachment.objects.filter(pk=voice.pk).exists()
        assert not _storage().exists(voice_key)

    def test_the_bucket_object_survives_if_the_transaction_never_commits(self, clinic_a):
        session = thread(clinic_a, texts=())
        voice = add_media(session, 'audio')
        ConversationSession.objects.filter(pk=session.pk).update(last_message_at=months_ago(25))

        run()   # `on_commit` no se ejecuta dentro de la transacción del test

        assert _storage().exists(voice.file.name)


class TestAgentMemory:
    def test_the_llm_context_of_that_phone_goes_too(self, clinic_a, clinic_b):
        thread(clinic_a)
        mine = [AgentMemory.objects.create(clinic=clinic_a, session_id=sid, messages=[SECRET])
                for sid in (PHONE, PHONE.lstrip('+'))]
        other_clinic = AgentMemory.objects.create(clinic=clinic_b, session_id=PHONE, messages=[SECRET])
        other_phone = AgentMemory.objects.create(clinic=clinic_a, session_id='+34600000000', messages=[SECRET])

        report = run()

        assert report.by_clinic[clinic_a.pk].memories == 2
        assert not AgentMemory.objects.filter(pk__in=[m.pk for m in mine]).exists()
        assert AgentMemory.objects.filter(pk__in=[other_clinic.pk, other_phone.pk]).count() == 2


class TestDryRun:
    def test_reports_but_changes_nothing(self, clinic_a):
        session = thread(clinic_a)
        voice = add_media(session, 'audio')
        ConversationSession.objects.filter(pk=session.pk).update(
            last_message_at=months_ago(25), last_message_preview=SECRET,
        )
        AgentMemory.objects.create(clinic=clinic_a, session_id=PHONE, messages=[SECRET])

        report = run(dry_run=True)

        assert report.messages == 3 and report.audio_files == 1
        assert ChatMessage.objects.filter(session=session).count() == 3
        assert ChatAttachment.objects.filter(pk=voice.pk).exists()
        assert AgentMemory.objects.count() == 1
        session.refresh_from_db()
        assert session.last_message_preview == SECRET
        assert not ChangeLog.objects.filter(user_repr='comando purge_conversations').exists()


class TestAuditTrail:
    def test_leaves_a_summary_per_clinic_without_any_message_text(self, clinic_a):
        thread(clinic_a)

        run()

        summary = ChangeLog.objects.get(object_repr__startswith='Purga de 2 mensajes de chat')
        assert summary.model_label == 'agent.ChatMessage'
        assert summary.changes['purged_messages'] == 2
        assert summary.changes['clinic_id'] == clinic_a.pk
        assert summary.changes['retention_months'] == 24
        assert SECRET not in str(summary.changes)

    def test_every_deleted_message_is_logged_as_a_deletion_without_its_content(self, clinic_a):
        thread(clinic_a)

        run()

        deletions = ChangeLog.objects.filter(model_label='agent.ChatMessage', action=ChangeLog.Action.DELETE)
        # Dos bajas individuales y el resumen.
        assert deletions.count() == 3
        assert SECRET not in str(list(deletions.values_list('changes', flat=True)))

    def test_nothing_to_purge_writes_no_summary(self, clinic_a):
        thread(clinic_a, last=months_ago(1))

        run()

        assert not ChangeLog.objects.filter(user_repr='comando purge_conversations').exists()


class TestCommandAndTask:
    def test_command_prints_what_it_did(self, clinic_a):
        thread(clinic_a)
        out = StringIO()

        call_command('purge_conversations', '--dry-run', stdout=out)
        assert 'DRY-RUN' in out.getvalue() and '2 mensajes se borrarían' in out.getvalue()
        assert ChatMessage.objects.count() == 2

        out = StringIO()
        call_command('purge_conversations', stdout=out)
        assert '2 mensajes borrados' in out.getvalue()
        assert ChatMessage.objects.count() == 0

    def test_command_says_so_when_there_is_nothing_to_do(self, clinic_a):
        out = StringIO()
        call_command('purge_conversations', stdout=out)
        assert 'Nada que purgar' in out.getvalue()

    def test_task_runs_the_purge(self, clinic_a):
        thread(clinic_a)

        purge_old_conversations()

        assert ChatMessage.objects.count() == 0

    def test_task_is_in_the_beat_schedule(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE['purge-old-conversations']
        assert entry['task'] == 'agent.tasks.purge_old_conversations'
