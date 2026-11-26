"""Clave del ZIP de una exportación en el bucket privado.

Mismas reglas que el resto de ficheros clínicos: un UUID, nunca un nombre
derivado del paciente, bajo su propio prefijo, y en la base solo se guarda la
clave (jamás una URL: las firmadas caducan y se generan en cada descarga).
"""
import uuid

PREFIX = 'exports'


def export_key() -> str:
    key = uuid.uuid4().hex
    return f'{PREFIX}/{key[:2]}/{key}.zip'


def export_upload_to(instance, filename):
    """`filename` se ignora: es la clave la que identifica el objeto."""
    return export_key()
