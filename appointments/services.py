from datetime import timedelta

from appointments.models import Appointment, AppointmentStatusHistory


def create_appointment(
    *,
    clinic,
    scheduled_at,
    service=None,
    end_at=None,
    status=None,
    actor=AppointmentStatusHistory.Actor.STAFF,
    actor_label='',
    **extra_fields,
):
    """
    Crea una cita y registra su estado inicial en el historial.

    Punto de entrada ÚNICO para dar de alta citas: lo comparten el
    AppointmentSerializer (API REST) y el AppointmentForm (panel web).

    - `scheduled_at` debe llegar ya en UTC (aware). La conversión desde el
      timezone de la clínica es responsabilidad de la capa que llama
      (serializer / form).
    - Si no se indica `end_at`, se calcula a partir de
      `service.duration_minutes` (30 min por defecto si no hay servicio).
    - Si no se indica `status`, la cita nace en 'pending'.
    """
    if end_at is None:
        duration = service.duration_minutes if service is not None else 30
        end_at = scheduled_at + timedelta(minutes=duration)

    status = status or Appointment.Status.PENDING

    appointment = Appointment.objects.create(
        clinic=clinic,
        scheduled_at=scheduled_at,
        service=service,
        end_at=end_at,
        status=status,
        **extra_fields,
    )

    AppointmentStatusHistory.objects.create(
        appointment=appointment,
        from_status=None,
        to_status=status,
        actor=actor,
        actor_label=actor_label,
    )

    return appointment
