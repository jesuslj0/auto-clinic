"""Episodio: cierre, bloqueo de visitas y cálculo de conservación."""
import pytest

from clinical.exceptions import EpisodeClosed
from clinical.models import Episode, Visit


@pytest.mark.django_db
class TestEpisodeClosing:
    def test_close_sets_discharge_date(self, episode_a):
        assert episode_a.status == Episode.Status.OPEN
        assert episode_a.discharged_at is None

        episode_a.close()

        assert episode_a.status == Episode.Status.CLOSED
        assert episode_a.discharged_at is not None

    def test_closed_episode_blocks_new_visits(self, episode_a, professional_a):
        episode_a.close()
        with pytest.raises(EpisodeClosed):
            Visit.objects.create(episode=episode_a, professional=professional_a)

    def test_reopen_allows_visits_again(self, episode_a, professional_a):
        episode_a.close()
        episode_a.reopen()

        assert episode_a.status == Episode.Status.OPEN
        assert episode_a.discharged_at is None
        visit = Visit.objects.create(episode=episode_a, professional=professional_a)
        assert visit.pk is not None


@pytest.mark.django_db
class TestRetention:
    def test_retention_expires_at_open_episode_is_none(self, episode_a):
        assert episode_a.retention_expires_at is None

    def test_retention_expires_at_uses_setting(self, episode_a, settings):
        settings.CLINICAL_RETENTION_YEARS = 10
        episode_a.close()

        discharged = episode_a.discharged_at
        expected = discharged.replace(year=discharged.year + 10)
        assert episode_a.retention_expires_at == expected
