"""Seguimiento de una lesión: una serie de observaciones, no un dato suelto.

Lo que se defiende aquí: la lesión no cambia (ni de sitio ni de identidad), lo
que cambia es lo que se ve de ella cada visita. Por eso las medidas viven en la
observación, y por eso una observación no se puede reasignar a otra lesión.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from clinical.exceptions import ProtectedClinicalRecord
from clinical.models import Episode, LesionObservation, Visit

from tests.clinical.test_lesions import make_lesion


@pytest.fixture
def lesion_a(db, episode_a):
    return make_lesion(episode_a)


def make_observation(lesion, visit, **overrides):
    data = {
        'lesion': lesion,
        'visit': visit,
        'length_mm': Decimal('12.0'),
        'width_mm': Decimal('8.0'),
        'description': 'Úlcera con bordes limpios',
    }
    data.update(overrides)
    return LesionObservation.objects.create(**data)


@pytest.mark.django_db
class TestMeasurements:
    def test_measurements_are_stored_as_numbers(self, lesion_a, visit_a):
        observation = make_observation(
            lesion_a, visit_a,
            length_mm=Decimal('12.5'), width_mm=Decimal('8.2'), depth_mm=Decimal('1.5'),
        )

        observation.refresh_from_db()
        assert observation.length_mm == Decimal('12.5')
        assert observation.width_mm == Decimal('8.2')
        assert observation.depth_mm == Decimal('1.5')

    def test_measurements_are_optional(self, lesion_a, visit_a):
        """No toda lesión se mide: una hiperqueratosis se describe."""
        observation = make_observation(
            lesion_a, visit_a,
            length_mm=None, width_mm=None, depth_mm=None,
            description='Hiperqueratosis difusa, sin solución de continuidad',
        )

        observation.refresh_from_db()
        assert observation.length_mm is None
        assert observation.description.startswith('Hiperqueratosis')

    def test_a_negative_measurement_is_rejected_by_the_database(self, lesion_a, visit_a):
        """Segundo nivel: el CheckConstraint, saltándose los validadores."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                LesionObservation.objects.create(
                    lesion=lesion_a, visit=visit_a, length_mm=Decimal('-1.0')
                )

    def test_full_clean_reports_the_field_at_fault(self, lesion_a, visit_a):
        observation = LesionObservation(
            lesion=lesion_a, visit=visit_a, width_mm=Decimal('-3.0')
        )
        with pytest.raises(ValidationError) as exc:
            observation.full_clean()
        assert 'width_mm' in exc.value.message_dict

    def test_the_observation_date_defaults_to_today(self, lesion_a, visit_a):
        assert make_observation(lesion_a, visit_a).observed_at == timezone.localdate()


@pytest.mark.django_db
class TestTheVisitMustMatchTheEpisode:
    """Una lesión y la visita en que se observa son del mismo proceso asistencial."""

    @pytest.fixture
    def other_episode_visit(self, history_a, professional_a):
        other = Episode.objects.create(
            history=history_a, reason='Otro proceso',
            responsible_professional=professional_a,
        )
        return Visit.objects.create(episode=other, professional=professional_a)

    def test_a_visit_from_another_episode_is_rejected(self, lesion_a, other_episode_visit):
        with pytest.raises(ValidationError) as exc:
            make_observation(lesion_a, other_episode_visit)
        assert 'visit' in exc.value.message_dict
        assert LesionObservation.objects.count() == 0

    def test_clean_rejects_it_too(self, lesion_a, other_episode_visit):
        observation = LesionObservation(lesion=lesion_a, visit=other_episode_visit)
        with pytest.raises(ValidationError):
            observation.clean()


@pytest.mark.django_db
class TestTheObservationIsNotReassigned:
    def test_it_cannot_change_lesion(self, lesion_a, visit_a, episode_a):
        observation = make_observation(lesion_a, visit_a)
        other_lesion = make_lesion(episode_a, anatomical_zone='heel', x=0.5, y=0.5)

        fresh = LesionObservation.objects.get(pk=observation.pk)
        fresh.lesion = other_lesion
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_it_cannot_change_visit(self, lesion_a, visit_a, episode_a, professional_a):
        observation = make_observation(lesion_a, visit_a)
        other_visit = Visit.objects.create(episode=episode_a, professional=professional_a)

        fresh = LesionObservation.objects.get(pk=observation.pk)
        fresh.visit = other_visit
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_findings_are_still_editable(self, lesion_a, visit_a):
        """Corregir una medida mal anotada es normal; reasignarla, no."""
        observation = make_observation(lesion_a, visit_a)
        observation.length_mm = Decimal('11.0')
        observation.description = 'Úlcera con bordes limpios, en reducción'
        observation.save()

        observation.refresh_from_db()
        assert observation.length_mm == Decimal('11.0')

    def test_a_mistaken_observation_is_soft_deleted(self, lesion_a, visit_a):
        observation = make_observation(lesion_a, visit_a)
        observation.delete()

        assert not LesionObservation.objects.filter(pk=observation.pk).exists()
        assert LesionObservation.all_objects.get(pk=observation.pk).deleted_at is not None


@pytest.mark.django_db
class TestTheSeries:
    """Una lesión agrega sus observaciones, y el orden importa."""

    @pytest.fixture
    def series(self, lesion_a, visit_a):
        today = timezone.localdate()
        return [
            make_observation(
                lesion_a, visit_a,
                observed_at=today - timedelta(days=days), length_mm=Decimal(length),
            )
            for days, length in ((14, '20.0'), (7, '15.0'), (0, '9.0'))
        ]

    def test_one_lesion_aggregates_many_observations(self, lesion_a, series):
        assert lesion_a.observations.count() == 3

    def test_the_evolution_reads_from_oldest_to_newest(self, lesion_a, series):
        lengths = [observation.length_mm for observation in lesion_a.evolution()]
        assert lengths == [Decimal('20.0'), Decimal('15.0'), Decimal('9.0')]

    def test_the_default_ordering_shows_the_latest_first(self, lesion_a, series):
        latest = lesion_a.observations.first()
        assert latest.observed_at == timezone.localdate()
        assert latest.length_mm == Decimal('9.0')

    def test_observations_of_another_lesion_do_not_mix(self, lesion_a, visit_a, series, episode_a):
        other_lesion = make_lesion(episode_a, anatomical_zone='heel', x=0.5, y=0.5)
        make_observation(other_lesion, visit_a, length_mm=Decimal('3.0'))

        assert lesion_a.observations.count() == 3
        assert other_lesion.observations.count() == 1

    def test_a_soft_deleted_observation_leaves_the_series(self, lesion_a, series):
        series[0].delete()

        assert lesion_a.observations.count() == 2
        assert [obs.length_mm for obs in lesion_a.evolution()] == [
            Decimal('15.0'), Decimal('9.0')
        ]

    def test_the_lesion_cannot_be_hard_deleted_with_observations(self, lesion_a, series):
        """PROTECT: la evolución no se queda huérfana por un borrado físico."""
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            with transaction.atomic():
                lesion_a.hard_delete()


@pytest.mark.django_db
class TestAudit:
    def test_the_observation_is_recorded_against_the_patient(self, lesion_a, visit_a, patient_a):
        from audit.models import ChangeLog

        observation = make_observation(lesion_a, visit_a)

        entry = ChangeLog.objects.filter(
            model_label='clinical.LesionObservation',
            object_id=str(observation.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert entry.patient_id == patient_a.pk
        # Las medidas se registran enteras: son con lo que se reconstruye la
        # evolución. La descripción, en cambio, es texto clínico y va enmascarada.
        assert entry.changes['length_mm']['after'] in ('12.0', Decimal('12.0'))
        assert entry.changes['description'] == {'changed': True}
        assert 'bordes limpios' not in str(entry.changes)


@pytest.mark.django_db
class TestAdmin:
    def test_observation_pages_are_reachable(self, admin_site_client, lesion_a, visit_a):
        observation = make_observation(lesion_a, visit_a)
        for url in (
            '/admin/clinical/lesionobservation/',
            f'/admin/clinical/lesionobservation/{observation.pk}/change/',
            '/admin/clinical/lesionobservation/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_the_lesion_and_visit_are_read_only_once_created(self, lesion_a, visit_a):
        from django.contrib.admin.sites import site

        observation = make_observation(lesion_a, visit_a)
        readonly = site._registry[LesionObservation].get_readonly_fields(
            request=None, obj=observation
        )
        assert {'lesion', 'visit'} <= set(readonly)
