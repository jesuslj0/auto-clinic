"""Alta de un procedimiento realizado: resolver la visita y registrarlo.

Un `PerformedProcedure` cuelga de una `Visit`, y una visita cuelga de un
`Episode`. Eso son tres filas para registrar «le he hecho una quiropodia», y las
dos puertas de entrada del panel —desde la cita y desde la ficha del paciente—
tienen que resolverlas **igual**. Vive aquí y no en cada vista por eso: dos
copias de la regla de «qué visita es esta» acabarían divergiendo, y la que
divergiera dejaría visitas duplicadas en la historia.

La regla de la visita, en una línea: **un encuentro es un encuentro**.

- Desde una cita, la visita es la de esa cita (`Visit.appointment`). Si ya
  existe se reutiliza; si no, se crea con los datos de la cita, que es quien
  sabe quién atendió y cuándo. Registrar dos procedimientos en la misma cita no
  puede abrir dos visitas.
- Desde la ficha no hay cita, así que el encuentro se identifica por episodio y
  día: se reutiliza la visita suelta de ese episodio en esa fecha, y si no hay,
  se abre una. Sin esto, anotar tres procedimientos del mismo día por separado
  dejaría tres visitas donde hubo una.

Nada de `bulk_create` aquí ni en ningún sitio que toque esta capa: se salta las
señales y el procedimiento quedaría sin auditar (ver `audit/README.md`).
"""
from __future__ import annotations

from django.db import transaction

from clinical.models import Episode, MedicalHistory, Visit


def _history_for(patient):
    """La historia del paciente, creándola si es de antes de la capa clínica."""
    history = MedicalHistory.objects.filter(patient=patient).first()
    if history is None:
        history = MedicalHistory.objects.create(patient=patient, clinic=patient.clinic)
    return history


def open_episode(patient, *, reason, professional=None) -> Episode:
    """Abre un episodio para el paciente con el motivo indicado."""
    return Episode.objects.create(
        history=_history_for(patient),
        reason=(reason or '').strip(),
        responsible_professional=professional,
    )


def visit_for_appointment(appointment, *, episode, professional=None) -> Visit:
    """La visita de esta cita: la que ya hay, o una nueva enganchada a ella.

    `professional` solo se usa al crearla, y solo si la cita no tiene profesional
    asignado — que es posible, porque `Appointment.professional` es nullable para
    no perder el historial al dar de baja a alguien.
    """
    existing = Visit.objects.filter(appointment=appointment).order_by('id').first()
    if existing is not None:
        return existing
    return Visit.objects.create(
        episode=episode,
        professional=appointment.professional or professional,
        appointment=appointment,
        occurred_at=appointment.scheduled_at,
    )


def visit_for_day(episode, *, moment, professional=None) -> Visit:
    """La visita suelta de ese episodio ese día, o una nueva.

    «Suelta» es `appointment__isnull=True` a propósito: si ese día hubo además
    una cita, su visita es la de la cita y tiene su propia puerta. Colgar aquí un
    procedimiento de la visita de una cita a la que no se ha entrado haría que la
    cita pasara a figurar «con procedimiento» sin que nadie la haya cerrado.
    """
    existing = (
        Visit.objects
        .filter(episode=episode, appointment__isnull=True, occurred_at__date=moment.date())
        .order_by('id')
        .first()
    )
    if existing is not None:
        return existing
    return Visit.objects.create(
        episode=episode, professional=professional, occurred_at=moment,
    )


@transaction.atomic
def record_procedure(form, *, patient, professional=None, appointment=None):
    """Registra el procedimiento del formulario. Devuelve el objeto creado.

    Todo dentro de una transacción: un episodio o una visita recién abiertos no
    pueden quedarse en la historia si el procedimiento no llega a guardarse.

    El importe NO se toca aquí. Lo congela `PerformedProcedure.save()`: si el
    formulario trae `frozen_price`, lo respeta; si no, copia el del catálogo en
    ese instante. El formulario ya garantiza que un servicio de precio variable
    no llega sin importe.
    """
    episode = form.fixed_episode
    if episode is None:
        episode = form.cleaned_data.get('episode') or open_episode(
            patient,
            reason=form.cleaned_data['episode_reason'],
            professional=professional,
        )

    moment = form.moment()
    if appointment is not None:
        visit = visit_for_appointment(appointment, episode=episode, professional=professional)
    else:
        visit = visit_for_day(episode, moment=moment, professional=professional)

    procedure = form.save(commit=False)
    procedure.visit = visit
    procedure.performed_at = moment
    procedure.created_by = professional
    procedure.save()
    return procedure
