"""Procedimientos realizados: el precio del catálogo, congelado el día que se hizo.

Lo que se defiende aquí es una sola idea con varias caras: **el catálogo es un
documento vivo y un procedimiento hecho no lo es**. Subir el precio de la
quiropodia no puede reescribir hacia atrás lo que costaron las del año pasado, ni
renombrar un servicio puede cambiar lo que pone en una visita de hace dos años.

Por eso el nombre y el precio se copian al registrar y no se vuelven a leer del
catálogo jamás; la FK al servicio se queda como procedencia, nunca como fuente de
verdad del importe.
"""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from clinical.exceptions import ProtectedClinicalRecord
from clinical.models import PerformedProcedure


@pytest.fixture
def procedure_a(db, visit_a, service_a):
    return PerformedProcedure.objects.create(visit=visit_a, service=service_a)


@pytest.mark.django_db
class TestTheSnapshotIsTakenFromTheCatalog:
    def test_creating_a_procedure_copies_the_current_price(self, visit_a, service_a):
        service_a.refresh_from_db()
        assert service_a.price == Decimal('50.00')

        procedure = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        assert procedure.frozen_price == Decimal('50.00')

    def test_creating_a_procedure_copies_the_current_name(self, visit_a, service_a):
        procedure = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        assert procedure.frozen_service_name == 'Consultation'

    def test_the_snapshot_survives_a_reload(self, visit_a, service_a):
        """No es un valor calculado al vuelo: está en columnas."""
        procedure = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        stored = PerformedProcedure.objects.get(pk=procedure.pk)
        assert stored.frozen_price == Decimal('50.00')
        assert stored.frozen_service_name == 'Consultation'

    def test_an_explicit_price_wins_over_the_catalog(self, visit_a, service_a):
        """Un servicio de precio variable se cobra por lo que se hizo."""
        procedure = PerformedProcedure.objects.create(
            visit=visit_a, service=service_a, frozen_price=Decimal('75.00'),
        )

        assert procedure.frozen_price == Decimal('75.00')

    def test_an_explicit_name_wins_over_the_catalog(self, visit_a, service_a):
        procedure = PerformedProcedure.objects.create(
            visit=visit_a, service=service_a,
            frozen_service_name='Consulta (primera visita)',
        )

        assert procedure.frozen_service_name == 'Consulta (primera visita)'

    def test_a_procedure_without_a_service_is_rejected(self, visit_a):
        with pytest.raises(ValidationError):
            PerformedProcedure.objects.create(visit=visit_a)
        assert PerformedProcedure.objects.count() == 0

    def test_a_negative_price_is_rejected_by_the_database(self, visit_a, service_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            PerformedProcedure.objects.create(
                visit=visit_a, service=service_a, frozen_price=Decimal('-10.00'),
            )


@pytest.mark.django_db
class TestTheCatalogNeverReachesBackwards:
    def test_changing_the_price_afterwards_does_not_move_the_procedure(
        self, procedure_a, service_a
    ):
        """El caso de siempre: en enero suben los precios."""
        service_a.price = Decimal('65.00')
        service_a.save()

        procedure_a.refresh_from_db()
        assert procedure_a.frozen_price == Decimal('50.00')
        assert PerformedProcedure.objects.get(pk=procedure_a.pk).frozen_price == Decimal('50.00')

    def test_renaming_the_service_afterwards_does_not_move_the_procedure(
        self, procedure_a, service_a
    ):
        service_a.name = 'Consulta podológica general'
        service_a.save()

        procedure_a.refresh_from_db()
        assert procedure_a.frozen_service_name == 'Consultation'

    def test_a_later_procedure_does_take_the_new_price(self, procedure_a, visit_a, service_a):
        """Congelado no es «fijo para siempre»: es «fijo el día que se hizo»."""
        service_a.price = Decimal('65.00')
        service_a.save()

        later = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        assert procedure_a.frozen_price == Decimal('50.00')
        assert later.frozen_price == Decimal('65.00')

    def test_saving_the_procedure_again_never_rereads_the_catalog(
        self, procedure_a, service_a
    ):
        """Ni siquiera un `save()` posterior vuelve a mirar el precio de hoy."""
        from clinical.models import Lesion

        service_a.price = Decimal('65.00')
        service_a.save()

        procedure_a.affected_zone = Lesion.AnatomicalZone.HEEL
        procedure_a.save()

        procedure_a.refresh_from_db()
        assert procedure_a.frozen_price == Decimal('50.00')

    def test_retiring_the_service_from_the_catalog_leaves_the_procedure_readable(
        self, procedure_a, service_a
    ):
        """El catálogo se limpia; lo que se cobró sigue ahí y sigue legible."""
        service_a.delete()

        stored = PerformedProcedure.objects.get(pk=procedure_a.pk)
        assert stored.frozen_service_name == 'Consultation'
        assert stored.frozen_price == Decimal('50.00')
        # La procedencia se queda colgando y se lee sin reventar.
        assert stored.catalog_service is None


@pytest.mark.django_db
class TestTheProcedureIsFrozen:
    def test_the_price_cannot_be_rewritten(self, procedure_a):
        fresh = PerformedProcedure.objects.get(pk=procedure_a.pk)
        fresh.frozen_price = Decimal('10.00')

        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_service_name_cannot_be_rewritten(self, procedure_a):
        fresh = PerformedProcedure.objects.get(pk=procedure_a.pk)
        fresh.frozen_service_name = 'Otra cosa'

        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_it_cannot_be_moved_to_another_visit(self, procedure_a, episode_a, professional_a):
        from clinical.models import Visit

        other = Visit.objects.create(episode=episode_a, professional=professional_a)

        fresh = PerformedProcedure.objects.get(pk=procedure_a.pk)
        fresh.visit = other
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_it_cannot_be_reassigned_to_another_service(self, procedure_a, clinic_a):
        from services.models import Service

        other = Service.objects.create(
            clinic=clinic_a, name='Quiropodia', duration_minutes=30, price='40.00',
        )

        fresh = PerformedProcedure.objects.get(pk=procedure_a.pk)
        fresh.service = other
        with pytest.raises(ProtectedClinicalRecord):
            fresh.save()

    def test_the_administrative_fields_stay_editable(self, procedure_a):
        """Congelado es el importe y el qué, no la anotación de dónde se hizo."""
        from clinical.models import Lesion

        procedure_a.affected_zone = Lesion.AnatomicalZone.HALLUX
        procedure_a.laterality = Lesion.Laterality.RIGHT
        procedure_a.save()

        procedure_a.refresh_from_db()
        assert procedure_a.affected_zone == Lesion.AnatomicalZone.HALLUX

    def test_correcting_means_deleting_and_registering_another(self, procedure_a, visit_a, service_a):
        procedure_a.delete()
        replacement = PerformedProcedure.objects.create(
            visit=visit_a, service=service_a, frozen_price=Decimal('35.00'),
        )

        assert not PerformedProcedure.objects.filter(pk=procedure_a.pk).exists()
        assert PerformedProcedure.all_objects.get(pk=procedure_a.pk).deleted_at is not None
        assert replacement.frozen_price == Decimal('35.00')

    def test_deleting_the_visit_cascades_to_its_procedures(self, visit_a, procedure_a):
        visit_a.delete()

        assert not PerformedProcedure.objects.filter(pk=procedure_a.pk).exists()
        assert PerformedProcedure.all_objects.get(pk=procedure_a.pk).deleted_at is not None


@pytest.mark.django_db
class TestMultiTenancy:
    def test_a_service_from_another_clinic_is_rejected(self, visit_a, service_b):
        """La FK no lleva restricción: el aislamiento lo pone el modelo."""
        with pytest.raises(ValidationError):
            PerformedProcedure.objects.create(visit=visit_a, service=service_b)
        assert PerformedProcedure.objects.count() == 0

    def test_full_clean_reports_it_on_the_service_field(self, visit_a, service_b):
        procedure = PerformedProcedure(visit=visit_a, service=service_b)

        with pytest.raises(ValidationError) as exc:
            procedure.clean()
        assert 'service' in exc.value.message_dict


@pytest.mark.django_db
class TestAudit:
    def test_the_procedure_is_recorded_with_its_amount(self, visit_a, service_a, patient_a):
        from audit.models import ChangeLog

        procedure = PerformedProcedure.objects.create(visit=visit_a, service=service_a)

        entry = ChangeLog.objects.filter(
            model_label='clinical.PerformedProcedure',
            object_id=str(procedure.pk),
            action=ChangeLog.Action.CREATE,
        ).get()
        assert entry.patient_id == patient_a.pk
        # Entero y sin enmascarar: si mañana se discute una factura, el log tiene
        # que poder decir qué se registró y por cuánto.
        assert entry.changes['frozen_price']['after'] == '50.00'
        assert entry.changes['frozen_service_name']['after'] == 'Consultation'


@pytest.mark.django_db
class TestAdmin:
    def test_procedure_pages_are_reachable(self, admin_site_client, procedure_a):
        for url in (
            '/admin/clinical/performedprocedure/',
            f'/admin/clinical/performedprocedure/{procedure_a.pk}/change/',
            '/admin/clinical/performedprocedure/add/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_the_frozen_fields_are_read_only_once_registered(
        self, admin_site_client, procedure_a
    ):
        from django.contrib.admin.sites import site

        model_admin = site._registry[PerformedProcedure]
        readonly = model_admin.get_readonly_fields(None, obj=procedure_a)

        assert 'frozen_price' in readonly
        assert 'frozen_service_name' in readonly
        assert 'service' in readonly
