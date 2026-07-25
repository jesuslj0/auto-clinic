"""Excepciones de la capa clínica.

Todas heredan de `core.managers.ProtectedRecordError` cuando representan un
intento de borrar algo imborrable, para que quien captura el contrato genérico
del borrado lógico las siga viendo.
"""
from core.managers import ProtectedRecordError


class ProtectedClinicalRecord(ProtectedRecordError):
    """Se intentó borrar un registro clínico que no admite borrado alguno.

    La historia clínica y las notas firmadas caen aquí: no se borran ni física,
    ni lógicamente, ni en cascada.
    """


class NoteAlreadySigned(Exception):
    """Se intentó modificar (o volver a firmar) una nota clínica ya firmada.

    Una nota firmada es inmutable: el único cambio posible es añadir una adenda.
    """


class EpisodeClosed(Exception):
    """Se intentó registrar una visita en un episodio cerrado.

    Un episodio cerrado no admite visitas nuevas sin reabrirse explícitamente.
    """
