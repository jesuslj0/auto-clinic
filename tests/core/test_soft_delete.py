"""El mixin de borrado lógico de `core` (`SoftDeleteModel`).

Se ejercita a través de dos modelos clínicos reales que lo consumen: `Episode`
(borrado lógico normal) y `MedicalHistory` (veto absoluto). Lo que se comprueba
es el comportamiento genérico del mixin, no la semántica clínica.
"""
import pytest

from clinical.models import Episode, MedicalHistory
from core.managers import ProtectedRecordError


@pytest.mark.django_db
class TestSoftDeleteHidesButKeeps:
    def test_instance_delete_is_soft(self, episode_a):
        pk = episode_a.pk
        episode_a.delete()

        assert episode_a.deleted_at is not None
        assert episode_a.is_deleted is True
        # Oculto del manager por defecto, presente en all_objects.
        assert not Episode.objects.filter(pk=pk).exists()
        assert Episode.all_objects.filter(pk=pk).exists()

    def test_is_deleted_is_property_not_field(self):
        # `is_deleted` no es un campo: no puede consultarse ni filtrarse en la BD.
        field_names = {f.name for f in Episode._meta.get_fields()}
        assert 'is_deleted' not in field_names
        assert 'deleted_at' in field_names

    def test_queryset_delete_is_soft(self, episode_a):
        Episode.objects.create(history=episode_a.history, reason="Segundo proceso")
        total = Episode.all_objects.count()

        Episode.objects.all().delete()

        # Nada se borró físicamente: siguen todas, marcadas.
        assert Episode.objects.count() == 0
        assert Episode.all_objects.count() == total
        assert all(e.deleted_at is not None for e in Episode.all_objects.all())

    def test_restore_brings_it_back(self, episode_a):
        episode_a.delete()
        episode_a.restore()
        assert episode_a.deleted_at is None
        assert Episode.objects.filter(pk=episode_a.pk).exists()


@pytest.mark.django_db
class TestCanBeDeletedVeto:
    def test_veto_blocks_instance_delete(self, history_a):
        # MedicalHistory.can_be_deleted() es siempre False.
        with pytest.raises(ProtectedRecordError):
            history_a.delete()
        assert MedicalHistory.all_objects.filter(pk=history_a.pk).exists()

    def test_veto_blocks_queryset_delete(self, history_a):
        with pytest.raises(ProtectedRecordError):
            MedicalHistory.objects.all().delete()
        assert MedicalHistory.all_objects.filter(pk=history_a.pk).exists()
