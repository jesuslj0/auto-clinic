"""Nota clínica SOAP: borrador editable, firma e inmutabilidad."""
import pytest
from django.db import DatabaseError, connection, transaction

from clinical.exceptions import NoteAlreadySigned
from clinical.hashing import compute_note_hash, verify_note_hash
from clinical.models import ClinicalNote, Visit
from core.managers import ProtectedRecordError


@pytest.mark.django_db
class TestDraftAndSign:
    def test_draft_note_is_editable(self, draft_note_a):
        draft_note_a.subjective = "Refiere mejoría"
        draft_note_a.save()
        draft_note_a.refresh_from_db()
        assert draft_note_a.subjective == "Refiere mejoría"

    def test_sign_sets_signer_date_and_hash(self, draft_note_a, professional_a):
        assert draft_note_a.status == ClinicalNote.Status.DRAFT
        assert draft_note_a.content_hash == ''

        draft_note_a.sign(professional_a)

        assert draft_note_a.status == ClinicalNote.Status.SIGNED
        assert draft_note_a.signed_by == professional_a
        assert draft_note_a.signed_at is not None
        assert draft_note_a.content_hash.startswith('sha256:')
        assert verify_note_hash(draft_note_a) is True

    def test_hash_covers_content_and_signature_metadata(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        assert draft_note_a.content_hash == compute_note_hash(draft_note_a)

    def test_re_signing_is_rejected(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        with pytest.raises(NoteAlreadySigned):
            draft_note_a.sign(professional_a)


@pytest.mark.django_db
class TestSignedNoteImmutabilityORM:
    def test_signed_note_save_is_blocked(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)

        fresh = ClinicalNote.objects.get(pk=draft_note_a.pk)
        fresh.subjective = "manipulado"
        with pytest.raises(NoteAlreadySigned):
            fresh.save()

    def test_signed_note_direct_delete_is_blocked(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        with pytest.raises(ProtectedRecordError):
            draft_note_a.delete()
        assert ClinicalNote.all_objects.filter(
            pk=draft_note_a.pk, deleted_at__isnull=True
        ).exists()

    def test_signed_note_queryset_delete_is_blocked(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        with pytest.raises(ProtectedRecordError):
            ClinicalNote.objects.filter(pk=draft_note_a.pk).delete()


@pytest.mark.django_db
class TestSignedNoteImmutabilityTrigger:
    """La barrera dura: SQL crudo, saltándose Django, choca con el trigger."""

    def test_raw_update_on_signed_note_raises(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE clinical_note SET subjective = 'hack' WHERE id = %s",
                        [draft_note_a.pk],
                    )
        draft_note_a.refresh_from_db()
        assert draft_note_a.subjective == "Refiere dolor al caminar"

    def test_raw_delete_on_signed_note_raises(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "DELETE FROM clinical_note WHERE id = %s", [draft_note_a.pk]
                    )
        assert ClinicalNote.all_objects.filter(pk=draft_note_a.pk).exists()

    def test_draft_note_can_still_be_signed_via_trigger(self, draft_note_a, professional_a):
        # La transición borrador→firmada es un UPDATE con OLD.status='draft': el
        # trigger la deja pasar.
        draft_note_a.sign(professional_a)
        draft_note_a.refresh_from_db()
        assert draft_note_a.status == ClinicalNote.Status.SIGNED


@pytest.mark.django_db
class TestSignedNoteNoCascadeDelete:
    def test_signed_note_blocks_visit_and_episode_delete(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        visit = draft_note_a.visit
        episode = visit.episode

        with pytest.raises(ProtectedRecordError):
            visit.delete()
        with pytest.raises(ProtectedRecordError):
            episode.delete()

        assert Visit.all_objects.filter(pk=visit.pk, deleted_at__isnull=True).exists()
        draft_note_a.refresh_from_db()
        assert draft_note_a.status == ClinicalNote.Status.SIGNED

    def test_draft_note_is_soft_deleted_by_cascade(self, draft_note_a):
        visit = draft_note_a.visit
        episode = visit.episode

        episode.delete()

        for obj in (episode, visit, draft_note_a):
            obj.refresh_from_db()
            assert obj.deleted_at is not None
