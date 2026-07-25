"""Señales de la capa clínica.

Auto-creación de la historia clínica: al alta de un paciente se crea su
`MedicalHistory`, de modo que NUNCA exista un paciente sin historia, sea cual sea
la vía de alta (`patients.services.create_patient` o `Patient.objects.create`
directo en tests y comandos).
"""
from django.db import transaction


def create_medical_history(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return

    from clinical.models import MedicalHistory

    # Idempotente: si por lo que sea ya tiene historia, no se duplica. Se mira con
    # `all_objects` porque una historia nunca se borra, pero por si acaso.
    if MedicalHistory.all_objects.filter(patient=instance).exists():
        return

    with transaction.atomic():
        MedicalHistory.objects.create(patient=instance, clinic=instance.clinic)
