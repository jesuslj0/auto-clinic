import re


def create_patient(*, clinic, phone, **fields):
    """
    Punto único de alta de pacientes: lo comparten el PatientSerializer
    (API REST) y el PatientCreateView (panel web).

    Normaliza el teléfono a E.164 antes de guardar. Lanza ValueError si el
    número no es válido.
    """
    from patients.models import Patient

    normalized = normalize_phone(phone)
    patient = Patient.objects.create(clinic=clinic, phone=normalized, **fields)
    # Las citas que se reservaron sin ficha con este teléfono pasan a ser suyas.
    # Llamada explícita y no señal, como el resto de derivaciones del proyecto.
    from appointments.services import link_orphan_appointments

    link_orphan_appointments(patient)
    return patient


def normalize_phone(phone: str) -> str:
    """
    Normaliza un número de teléfono al formato E.164 (+XXXXXXXXXXX).

    Reglas:
    - Elimina todos los caracteres no numéricos excepto el '+' inicial.
    - Si empieza por '+', usa los dígitos que siguen como número completo.
    - Si tiene 9 dígitos y empieza por 6, 7, 8 o 9: asume España (prefijo 34).
    - Si ya tiene prefijo de país (10-15 dígitos sin '+'): usa tal cual.
    - Valida que el resultado tenga entre 7 y 15 dígitos tras el '+'.
    - Lanza ValueError si no puede normalizar.
    """
    if not phone or not phone.strip():
        raise ValueError("El número de teléfono no puede estar vacío.")

    raw = phone.strip()

    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)

    if not digits:
        raise ValueError(f"No se encontraron dígitos en '{phone}'.")

    if has_plus:
        normalized_digits = digits
    elif len(digits) == 9 and digits[0] in "6789":
        normalized_digits = "34" + digits
    elif 10 <= len(digits) <= 15:
        normalized_digits = digits
    else:
        raise ValueError(
            f"No se puede determinar el prefijo de país para '{phone}'. "
            "Proporcione el número en formato E.164 (ej. +34612345678)."
        )

    if not (7 <= len(normalized_digits) <= 15):
        raise ValueError(
            f"El número normalizado tiene {len(normalized_digits)} dígitos; "
            "debe tener entre 7 y 15."
        )

    return "+" + normalized_digits


def normalize_phone_safe(phone: str) -> str | None:
    """
    Como normalize_phone pero devuelve None en lugar de lanzar excepción.
    Útil para migraciones de datos donde se quieren omitir registros inválidos.
    """
    try:
        return normalize_phone(phone)
    except (ValueError, AttributeError):
        return None


def upcoming_appointments(patient):
    """Citas futuras del paciente que siguen en pie (pendientes, confirmadas o movidas)."""
    from django.utils import timezone

    from appointments.models import LIVE_STATUSES, Appointment

    return (
        Appointment.objects.filter(
            patient=patient, scheduled_at__gte=timezone.now(), status__in=LIVE_STATUSES
        )
        .select_related('service', 'professional__user')
        .order_by('scheduled_at')
    )


def archive_patient(patient, *, user):
    """Archiva la ficha y cancela sus citas futuras, todo o nada.

    Devuelve cuántas citas se cancelaron. Cada cita pasa por `cancel_appointment()`
    (una a una, con `save()`): deja su historial de estados y no se salta las
    señales ni la auditoría, que un `queryset.update()` sí haría.
    """
    from django.db import transaction

    from appointments.models import Appointment, AppointmentStatusHistory
    from appointments.services import cancel_appointment

    actor_label = (user.get_full_name() or user.email) if user else ''
    with transaction.atomic():
        cancelled = 0
        for appointment in upcoming_appointments(patient).select_for_update(of=('self',)):
            cancel_appointment(
                appointment,
                actor=AppointmentStatusHistory.Actor.STAFF,
                actor_label=actor_label,
                cancelled_by=Appointment.CancelledBy.STAFF,
            )
            cancelled += 1
        patient.archive(by=user)
    return cancelled
