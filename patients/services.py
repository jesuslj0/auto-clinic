import re


def create_patient(*, clinic, phone, **fields):
    """
    Punto único de alta de pacientes: lo comparten el PatientSerializer
    (API REST) y el PatientCreateView (panel web).

    Normaliza el teléfono a E.164 antes de guardar. Lanza ValueError si el
    número no es válido.
    """
    from patients.models import Patient

    normalized = normalize_phone(phone) if (phone or '').strip() else ''
    patient = Patient.objects.create(clinic=clinic, phone=normalized, **fields)
    # Si ese teléfono ya es de un contacto que aún no tiene ficha propia, esta es
    # la suya: así puede reservar también para sí mismo.
    if normalized:
        from patients.models import Guardian

        guardian = Guardian.objects.filter(clinic=clinic, phone=normalized, patient__isnull=True).first()
        if guardian is not None:
            guardian.patient = patient
            guardian.save(update_fields=['patient', 'updated_at'])
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


def add_guardian(patient, *, first_name, last_name='', phone, relationship, created_by=None):
    """Vincula un contacto responsable a un paciente. Devuelve `(vínculo, contacto_nuevo)`.

    El contacto se identifica por su teléfono dentro de la clínica: si ya existe
    (porque lo es de otro paciente) se reutiliza y no se pisan sus datos. Si el
    teléfono es el de una ficha de paciente de la clínica (y solo una), el contacto
    queda enlazado a ella. Lanza `ValueError` si el teléfono no es válido o si el
    paciente intenta ser contacto de sí mismo.
    """
    from django.db import transaction

    from patients.models import Guardian, Patient, PatientGuardian

    normalized = normalize_phone(phone)
    if patient.phone and patient.phone == normalized:
        raise ValueError('El contacto no puede usar el mismo teléfono que el propio paciente.')

    with transaction.atomic():
        guardian = Guardian.objects.filter(clinic=patient.clinic, phone=normalized).first()
        created = guardian is None
        if created:
            own = list(
                Patient.objects.filter(
                    clinic=patient.clinic, phone=normalized, archived_at__isnull=True
                ).exclude(pk=patient.pk)[:2]
            )
            guardian = Guardian.objects.create(
                clinic=patient.clinic,
                first_name=first_name.strip(),
                last_name=last_name.strip(),
                phone=normalized,
                patient=own[0] if len(own) == 1 else None,
            )
        if guardian.patient_id == patient.pk:
            raise ValueError('El paciente no puede ser contacto de sí mismo.')
        link, linked = PatientGuardian.objects.get_or_create(
            patient=patient,
            guardian=guardian,
            defaults={'relationship': relationship, 'created_by': created_by},
        )
        if not linked:
            raise ValueError(f'{guardian} ya es contacto de este paciente.')
    return link, created


def booking_context(clinic, phone):
    """Qué pacientes puede gestionar el número de WhatsApp que escribe.

    `ask_for_whom` es True cuando el número es el de un contacto responsable con
    al menos un paciente vinculado: el agente debe preguntar «¿para quién?» antes
    de reservar, cancelar o mover una cita. Sin contacto (o sin vinculados) el
    número es lo que siempre ha sido y no se pregunta nada.

    `candidates` son SOLO los pacientes de ese contacto —vinculados y, si lo es,
    él mismo—, nunca otros de la clínica.
    """
    from patients.models import Guardian

    normalized = normalize_phone_safe(phone) or (phone or '').strip()
    guardian = Guardian.objects.filter(clinic=clinic, phone=normalized).first()
    if guardian is None:
        return {'ask_for_whom': False, 'guardian': None, 'candidates': []}

    candidates = [
        {
            'id': link.patient_id,
            'name': str(link.patient),
            'relationship': link.relationship,
            'relationship_display': link.get_relationship_display(),
            'is_self': False,
        }
        for link in guardian.links.select_related('patient').order_by('created_at')
        if link.patient.archived_at is None
    ]
    own = guardian.patient if guardian.patient_id and guardian.patient.archived_at is None else None
    if own is not None and all(c['id'] != own.pk for c in candidates):
        candidates.insert(0, {
            'id': own.pk, 'name': str(own), 'relationship': 'self',
            'relationship_display': 'Él/ella mismo/a', 'is_self': True,
        })
    return {
        'ask_for_whom': any(not c['is_self'] for c in candidates),
        'guardian': {
            'name': str(guardian),
            'first_name': guardian.first_name,
            'last_name': guardian.last_name,
            'has_own_file': own is not None,
        },
        'candidates': candidates,
    }


def can_book_for(clinic, phone, patient):
    """¿Puede el número `phone` reservar para `patient`? Ni más ni menos que lo suyo.

    Vale si la ficha tiene ese mismo teléfono (el paciente que escribe por sí
    mismo) o si el número es el de un contacto cuyo `bookable_patients()` la
    incluye.
    """
    from patients.models import Guardian

    if patient.clinic_id != clinic.pk:
        return False
    normalized = normalize_phone_safe(phone) or (phone or '').strip()
    if normalized and patient.phone == normalized:
        return True
    guardian = Guardian.objects.filter(clinic=clinic, phone=normalized).first()
    return guardian is not None and guardian.bookable_patients().filter(pk=patient.pk).exists()


def create_patient_from_guardian(guardian):
    """Abre la ficha de paciente de un contacto responsable que aún no la tiene.

    Devuelve `(paciente, creado)`. Si la clínica ya tiene una ficha activa con el
    teléfono del contacto (y solo una) se enlaza en vez de duplicar. Si no, se crea
    con su nombre, teléfono y correo, y `create_patient()` la enlaza al contacto.
    Lanza `ValueError` si el contacto ya tiene ficha o los datos no bastan.
    """
    from django.db import transaction

    from patients.models import Patient

    if guardian.patient_id:
        raise ValueError(f'{guardian} ya tiene ficha de paciente.')

    with transaction.atomic():
        existing = list(
            Patient.objects.filter(
                clinic=guardian.clinic, phone=guardian.phone, archived_at__isnull=True
            )[:2]
        )
        if len(existing) == 1:
            guardian.patient = existing[0]
            guardian.save(update_fields=['patient', 'updated_at'])
            return existing[0], False

        email = guardian.email
        if email and Patient.objects.filter(
            clinic=guardian.clinic, email=email, phone=guardian.phone
        ).exists():
            email = ''
        patient = create_patient(
            clinic=guardian.clinic,
            phone=guardian.phone,
            first_name=guardian.first_name,
            last_name=guardian.last_name,
            email=email,
        )
        guardian.refresh_from_db(fields=['patient'])
        if guardian.patient_id is None:
            guardian.patient = patient
            guardian.save(update_fields=['patient', 'updated_at'])
    return patient, True

