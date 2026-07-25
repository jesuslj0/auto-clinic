"""Inmutabilidad por debajo del ORM: el trigger de PostgreSQL.

`test_immutability.py` cubre las puertas del ORM. Aquí se comprueba la barrera
que hay debajo: un `UPDATE`/`DELETE` en SQL crudo, saltándose Django por
completo, tiene que chocar igualmente con el trigger `audit_prevent_modification`.

Estos tests usan `transaction=True` a propósito: la válvula se pone con
`SET LOCAL`, que vive lo que vive su transacción. Solo con transacciones reales
(y no con los savepoints envolventes de un test normal) se puede demostrar que
la válvula NO persiste una vez cerrada la transacción de purga.
"""
import pytest
from django.db import DatabaseError, connection, transaction

from audit.models import AccessLog, ChangeLog

TABLES = {
    ChangeLog: 'audit_change_log',
    AccessLog: 'audit_access_log',
}


def _make_change_row():
    return ChangeLog.objects.create(
        action=ChangeLog.Action.CREATE,
        model_label='patients.Patient',
        object_repr='Fila cruda',
    )


def _make_access_row():
    return AccessLog.objects.create(
        action=AccessLog.Action.VIEW,
        object_repr='Fila cruda',
    )


@pytest.mark.django_db(transaction=True)
class TestRawUpdateIsBlocked:
    """UPDATE nunca se permite, ni siquiera con la válvula puesta."""

    def test_raw_update_on_change_log_raises(self):
        row = _make_change_row()
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE audit_change_log SET action = 'update' WHERE id = %s",
                        [row.pk],
                    )
        assert ChangeLog.objects.filter(pk=row.pk, action='create').exists()

    def test_raw_update_on_access_log_raises(self):
        row = _make_access_row()
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE audit_access_log SET action = 'list' WHERE id = %s",
                        [row.pk],
                    )
        assert AccessLog.objects.filter(pk=row.pk, action='view').exists()

    def test_update_is_blocked_even_with_the_valve(self):
        """La válvula es SOLO para DELETE: un UPDATE choca aunque esté puesta."""
        row = _make_change_row()
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute("SET LOCAL audit.purga = 'on'")
                    cur.execute(
                        "UPDATE audit_change_log SET action = 'update' WHERE id = %s",
                        [row.pk],
                    )
        assert ChangeLog.objects.filter(pk=row.pk, action='create').exists()


@pytest.mark.django_db(transaction=True)
class TestRawDeleteNeedsTheValve:
    def test_raw_delete_without_valve_raises(self):
        row = _make_change_row()
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        'DELETE FROM audit_change_log WHERE id = %s', [row.pk]
                    )
        assert ChangeLog.objects.filter(pk=row.pk).exists()

    def test_raw_delete_on_access_log_without_valve_raises(self):
        row = _make_access_row()
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        'DELETE FROM audit_access_log WHERE id = %s', [row.pk]
                    )
        assert AccessLog.objects.filter(pk=row.pk).exists()

    def test_raw_delete_with_valve_succeeds(self):
        row = _make_change_row()
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL audit.purga = 'on'")
                cur.execute('DELETE FROM audit_change_log WHERE id = %s', [row.pk])
        assert not ChangeLog.objects.filter(pk=row.pk).exists()


@pytest.mark.django_db(transaction=True)
class TestValveDoesNotPersist:
    def test_valve_dies_with_its_transaction(self):
        """Tras la transacción con la válvula, un DELETE vuelve a estar vetado."""
        purged = _make_change_row()
        survivor = _make_change_row()

        # Transacción de purga: la válvula autoriza el borrado de `purged`.
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL audit.purga = 'on'")
                cur.execute('DELETE FROM audit_change_log WHERE id = %s', [purged.pk])

        assert not ChangeLog.objects.filter(pk=purged.pk).exists()

        # Ya fuera de esa transacción, el SET LOCAL ha muerto: el DELETE de
        # `survivor` choca de nuevo con el trigger.
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        'DELETE FROM audit_change_log WHERE id = %s', [survivor.pk]
                    )
        assert ChangeLog.objects.filter(pk=survivor.pk).exists()
