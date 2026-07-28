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


class TemplateVersionPublished(Exception):
    """Se intentó modificar una versión de cuestionario ya publicada o sus preguntas.

    Una versión publicada es el documento que se respondió: su contenido queda
    congelado. Para cambiar el cuestionario se publica una versión nueva.
    """


class TemplateVersionNotPublished(Exception):
    """Se intentó responder (o marcar como vigente) una versión en borrador.

    Solo se responde lo que está publicado: un borrador aún puede cambiar, y una
    respuesta debe poder señalar siempre a un documento estable.
    """
