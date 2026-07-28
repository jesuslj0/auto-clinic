"""Lesiones sobre el mapa del pie: coordenadas normalizadas y zona clínica.

La idea que se defiende aquí: **la zona anatómica y las coordenadas son dos datos
distintos**. La zona es lo clínico y sobrevive a un rediseño del SVG; las
coordenadas solo sirven para pintar. Nunca se deriva una de la otra.
"""
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from clinical.exceptions import ProtectedClinicalRecord
from clinical.models import Lesion


def make_lesion(episode, **overrides):
    data = {
        'episode': episode,
        'laterality': Lesion.Laterality.LEFT,
        'view': Lesion.View.PLANTAR,
        'anatomical_zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
        'x': 0.42,
        'y': 0.63,
        'lesion_type': Lesion.LesionType.HYPERKERATOSIS,
    }
    data.update(overrides)
    return Lesion.objects.create(**data)


@pytest.fixture
def lesion_a(db, episode_a):
    return make_lesion(episode_a)


@pytest.mark.django_db
class TestCoordinates:
    def test_normalized_coordinates_are_accepted(self, episode_a):
        lesion = make_lesion(episode_a, x=0.0, y=1.0)
        lesion.refresh_from_db()
        assert (lesion.x, lesion.y) == (0.0, 1.0)

    @pytest.mark.parametrize('coords', [
        {'x': 1.5, 'y': 0.5},      # x por encima
        {'x': -0.1, 'y': 0.5},     # x por debajo
        {'x': 0.5, 'y': 42.0},     # y en píxeles, el error clásico
        {'x': 0.5, 'y': -0.01},
    ])
    def test_coordinates_outside_the_range_are_rejected(self, episode_a, coords):
        with pytest.raises(ValidationError):
            make_lesion(episode_a, **coords)
        assert Lesion.objects.count() == 0

    def test_pixel_coordinates_are_rejected_on_full_clean_too(self, episode_a):
        """Los validadores del campo también lo cazan en formularios y admin."""
        lesion = Lesion(
            episode=episode_a, laterality=Lesion.Laterality.RIGHT,
            view=Lesion.View.DORSAL, anatomical_zone=Lesion.AnatomicalZone.HEEL,
            x=320.0, y=180.0,
        )
        with pytest.raises(ValidationError) as exc:
            lesion.full_clean()
        assert 'x' in exc.value.message_dict

    def test_the_database_rejects_them_too(self, episode_a, lesion_a):
        """Segundo nivel: el CheckConstraint, saltándose el ORM."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Lesion.all_objects.filter(pk=lesion_a.pk).update(x=7.0)


@pytest.mark.django_db
class TestStatusAndResolution:
    def test_a_new_lesion_is_active_without_resolution_date(self, lesion_a):
        assert lesion_a.status == Lesion.Status.ACTIVE
        assert lesion_a.resolved_at is None
        assert lesion_a.detected_at == timezone.localdate()

    def test_resolved_without_a_date_is_rejected(self, episode_a):
        with pytest.raises(ValidationError):
            make_lesion(episode_a, status=Lesion.Status.RESOLVED)

    def test_active_with_a_resolution_date_is_rejected(self, episode_a):
        with pytest.raises(ValidationError):
            make_lesion(
                episode_a, status=Lesion.Status.ACTIVE, resolved_at=date(2026, 7, 1)
            )

    def test_clean_reports_the_field_at_fault(self, episode_a):
        lesion = Lesion(
            episode=episode_a, laterality=Lesion.Laterality.LEFT,
            view=Lesion.View.PLANTAR, anatomical_zone=Lesion.AnatomicalZone.HEEL,
            x=0.5, y=0.5, status=Lesion.Status.RESOLVED,
        )
        with pytest.raises(ValidationError) as exc:
            lesion.clean()
        assert 'resolved_at' in exc.value.message_dict

    def test_resolve_sets_status_and_date_together(self, lesion_a):
        lesion_a.resolve()

        lesion_a.refresh_from_db()
        assert lesion_a.status == Lesion.Status.RESOLVED
        assert lesion_a.resolved_at == timezone.localdate()

    def test_resolve_accepts_an_explicit_date(self, lesion_a):
        yesterday = timezone.localdate() - timedelta(days=1)
        lesion_a.resolve(on=yesterday)

        lesion_a.refresh_from_db()
        assert lesion_a.resolved_at == yesterday

    def test_reopen_clears_the_resolution_date(self, lesion_a):
        lesion_a.resolve()
        lesion_a.reopen()

        lesion_a.refresh_from_db()
        assert lesion_a.status == Lesion.Status.ACTIVE
        assert lesion_a.resolved_at is None

    def test_resolve_and_reopen_are_idempotent(self, lesion_a):
        lesion_a.resolve()
        first = lesion_a.resolved_at
        lesion_a.resolve()
        lesion_a.refresh_from_db()
        assert lesion_a.resolved_at == first

        lesion_a.reopen()
        lesion_a.reopen()
        lesion_a.refresh_from_db()
        assert lesion_a.status == Lesion.Status.ACTIVE

    def test_the_database_rejects_an_incoherent_status(self, lesion_a):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                # Estado resuelto sin fecha, saltándose el ORM.
                Lesion.all_objects.filter(pk=lesion_a.pk).update(status='resolved')


@pytest.mark.django_db
class TestLocationIsFixed:
    def test_a_lesion_cannot_be_moved(self, lesion_a):
        lesion_a.x = 0.9
        with pytest.raises(ProtectedClinicalRecord):
            lesion_a.save()

        lesion_a.refresh_from_db()
        assert lesion_a.x == 0.42

    def test_the_anatomical_zone_is_fixed_too(self, lesion_a):
        fresh = Lesion.objects.get(pk=lesion_a.pk)
        fresh.anatomical_zone = Lesion.AnatomicalZone.HEEL
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_a_lesion_cannot_change_episode(self, lesion_a, history_a, professional_a):
        from clinical.models import Episode

        other = Episode.objects.create(
            history=history_a, reason='Otro proceso', responsible_professional=professional_a
        )
        fresh = Lesion.objects.get(pk=lesion_a.pk)
        fresh.episode = other
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_lifecycle_is_still_editable(self, lesion_a):
        """Lo que cambia es la evolución, no el sitio."""
        lesion_a.lesion_type = Lesion.LesionType.ULCER
        lesion_a.save()
        lesion_a.resolve()

        lesion_a.refresh_from_db()
        assert lesion_a.lesion_type == Lesion.LesionType.ULCER
        assert lesion_a.status == Lesion.Status.RESOLVED

    def test_a_mistaken_lesion_is_soft_deleted(self, lesion_a):
        lesion_a.delete()

        assert not Lesion.objects.filter(pk=lesion_a.pk).exists()
        assert Lesion.all_objects.get(pk=lesion_a.pk).deleted_at is not None


@pytest.mark.django_db
class TestForView:
    @pytest.fixture
    def lesions(self, episode_a):
        return {
            'left_plantar': make_lesion(
                episode_a, laterality=Lesion.Laterality.LEFT, view=Lesion.View.PLANTAR,
            ),
            'left_dorsal': make_lesion(
                episode_a, laterality=Lesion.Laterality.LEFT, view=Lesion.View.DORSAL,
            ),
            'right_plantar': make_lesion(
                episode_a, laterality=Lesion.Laterality.RIGHT, view=Lesion.View.PLANTAR,
            ),
        }

    def test_returns_only_that_foot_and_that_view(self, episode_a, lesions):
        result = Lesion.objects.for_view(
            episode_a, Lesion.Laterality.LEFT, Lesion.View.PLANTAR
        )
        assert list(result) == [lesions['left_plantar']]

    def test_ignores_lesions_of_another_episode(self, episode_a, lesions, history_a, professional_a):
        from clinical.models import Episode

        other_episode = Episode.objects.create(
            history=history_a, reason='Otro proceso', responsible_professional=professional_a
        )
        make_lesion(
            other_episode, laterality=Lesion.Laterality.LEFT, view=Lesion.View.PLANTAR,
        )

        result = Lesion.objects.for_view(
            episode_a, Lesion.Laterality.LEFT, Lesion.View.PLANTAR
        )
        assert list(result) == [lesions['left_plantar']]

    def test_chains_with_the_other_helpers(self, episode_a, lesions):
        lesions['left_plantar'].resolve()

        view = Lesion.objects.for_view(episode_a, Lesion.Laterality.LEFT, Lesion.View.PLANTAR)
        assert view.active().count() == 0
        assert view.resolved().count() == 1

    def test_a_soft_deleted_lesion_disappears_from_the_map(self, episode_a, lesions):
        lesions['left_plantar'].delete()

        result = Lesion.objects.for_view(
            episode_a, Lesion.Laterality.LEFT, Lesion.View.PLANTAR
        )
        assert list(result) == []


@pytest.mark.django_db
class TestAnatomicalZoneIsIndependent:
    """La zona es el dato clínico; las coordenadas, solo el dibujo."""

    def test_the_zone_is_queryable_without_touching_coordinates(self, episode_a):
        make_lesion(
            episode_a, anatomical_zone=Lesion.AnatomicalZone.HALLUX, x=0.11, y=0.12,
        )
        make_lesion(
            episode_a, anatomical_zone=Lesion.AnatomicalZone.HALLUX, x=0.88, y=0.91,
        )
        make_lesion(episode_a, anatomical_zone=Lesion.AnatomicalZone.HEEL, x=0.5, y=0.5)

        hallux = Lesion.objects.filter(anatomical_zone=Lesion.AnatomicalZone.HALLUX)
        assert hallux.count() == 2
        # Misma zona, coordenadas distintas: son datos independientes.
        assert len({(lesion.x, lesion.y) for lesion in hallux}) == 2

    def test_the_zone_survives_coordinates_that_no_longer_fit_the_svg(self, episode_a):
        """Un rediseño del SVG invalida el punto, nunca el dato clínico."""
        lesion = make_lesion(episode_a, anatomical_zone=Lesion.AnatomicalZone.FIFTH_METATARSAL)

        stored = Lesion.objects.get(pk=lesion.pk)
        assert stored.anatomical_zone == 'fifth_metatarsal'
        assert stored.get_anatomical_zone_display() == 'Quinto metatarsiano'

    def test_the_zone_is_coded_not_free_text(self, episode_a):
        lesion = Lesion(
            episode=episode_a, laterality=Lesion.Laterality.LEFT,
            view=Lesion.View.PLANTAR, anatomical_zone='primer metatarsiano (a mano)',
            x=0.5, y=0.5,
        )
        with pytest.raises(ValidationError):
            lesion.full_clean()


@pytest.mark.django_db
class TestAudit:
    def test_the_lesion_and_its_resolution_are_recorded_against_the_patient(self, lesion_a, patient_a):
        from audit.models import ChangeLog

        created = ChangeLog.objects.filter(
            model_label='clinical.Lesion', object_id=str(lesion_a.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert created.patient_id == patient_a.pk
        assert created.changes['anatomical_zone']['after'] == 'first_metatarsal'

        lesion_a.resolve()

        updated = ChangeLog.objects.filter(
            model_label='clinical.Lesion', object_id=str(lesion_a.pk),
            action=ChangeLog.Action.UPDATE,
        ).latest('timestamp')
        assert updated.changes['status'] == {'before': 'active', 'after': 'resolved'}


@pytest.mark.django_db
class TestAdmin:
    def test_lesion_pages_are_reachable(self, admin_site_client, lesion_a):
        for url in (
            '/admin/clinical/lesion/',
            f'/admin/clinical/lesion/{lesion_a.pk}/change/',
            '/admin/clinical/lesion/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_the_location_is_read_only_once_created(self, lesion_a):
        from django.contrib.admin.sites import site

        readonly = site._registry[Lesion].get_readonly_fields(request=None, obj=lesion_a)
        assert {'episode', 'laterality', 'view', 'anatomical_zone', 'x', 'y'} <= set(readonly)

    def test_resolve_action_sets_status_and_date(self, admin_site_client, lesion_a):
        response = admin_site_client.post(
            '/admin/clinical/lesion/',
            {'action': 'resolve_lesions', '_selected_action': [str(lesion_a.pk)]},
            follow=True,
        )
        assert response.status_code == 200

        lesion_a.refresh_from_db()
        assert lesion_a.status == Lesion.Status.RESOLVED
        assert lesion_a.resolved_at is not None
