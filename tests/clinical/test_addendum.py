"""Adenda: de solo inserción y sin efecto sobre el hash de la nota."""
import pytest
from django.db import DatabaseError, connection, transaction

from clinical.models import Addendum
from core.managers import ProtectedRecordError


@pytest.mark.django_db
class TestAddendum:
    def test_addendum_does_not_alter_note_hash(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        original_hash = draft_note_a.content_hash

        Addendum.objects.create(
            note=draft_note_a, author=professional_a, text="Se añade evolución favorable."
        )

        draft_note_a.refresh_from_db()
        assert draft_note_a.content_hash == original_hash

    def test_addendum_cannot_be_edited(self, draft_note_a, professional_a):
        addendum = Addendum.objects.create(
            note=draft_note_a, author=professional_a, text="Original"
        )
        addendum.text = "Modificado"
        with pytest.raises(ProtectedRecordError):
            addendum.save()

    def test_addendum_cannot_be_deleted(self, draft_note_a, professional_a):
        addendum = Addendum.objects.create(
            note=draft_note_a, author=professional_a, text="Permanente"
        )
        with pytest.raises(ProtectedRecordError):
            addendum.delete()
        with pytest.raises(ProtectedRecordError):
            Addendum.objects.filter(pk=addendum.pk).delete()

    def test_addendum_trigger_blocks_raw_update(self, draft_note_a, professional_a):
        addendum = Addendum.objects.create(
            note=draft_note_a, author=professional_a, text="Intocable"
        )
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE clinical_addendum SET text = 'x' WHERE id = %s",
                        [addendum.pk],
                    )
