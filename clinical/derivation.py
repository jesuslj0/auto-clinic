"""Motor de derivación: de una anamnesis contestada a las alertas de la ficha.

La evaluación de las reglas vive en `clinical/rules.py` y es pura. Aquí está lo
otro: convertir esas intenciones en filas, sin duplicar y sin pisar nunca lo que
haya escrito una persona.

Tres invariantes, por orden de importancia:

1. **Las alertas manuales no se tocan jamás.** Todas las consultas de este módulo
   filtran por `source='derived'`. Que un motor apague un aviso que puso un
   profesional sería el peor fallo posible de esta capa.
2. **Idempotencia.** Una alerta derivada se identifica por
   `(paciente, tipo, respuesta de origen)`. Ejecutar la derivación dos veces
   sobre la misma respuesta no crea nada nuevo. Además de resolverse aquí, está
   respaldado por un índice único parcial en la base de datos.
3. **Corregir es desactivar, nunca borrar.** Si una anamnesis nueva ya no
   sostiene una condición, la alerta anterior queda con `is_active=False` y su
   fila intacta: hay que poder responder a «esto se sabía en aquel momento».

**Reemplazo entre respuestas.** Cuando se contesta de nuevo el mismo
cuestionario, las alertas derivadas de respuestas ANTERIORES de ese mismo
cuestionario se apagan todas, sostenga o no la nueva las mismas condiciones. Es
más de lo que exige el mínimo —apagar solo las que dejan de sostenerse—, y es a
propósito: si se dejaran vivas las que siguen sostenidas, la ficha acabaría con
dos alertas idénticas de la misma condición, una por respuesta. La condición que
siga vigente se vuelve a levantar acto seguido desde la respuesta nueva, así que
lo que ve la ficha es una sola alerta, y la historia de la anterior se conserva
apagada. Las derivadas de OTRO cuestionario no se tocan: son otra fuente.
"""
from dataclasses import dataclass, field

from django.db import transaction

from clinical.models import ClinicalAlert
from clinical.rules import evaluate_snapshot


@dataclass
class DerivationResult:
    """Qué hizo la derivación. Para tests, logs y para no adivinar."""

    created: list = field(default_factory=list)
    reactivated: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    deactivated: list = field(default_factory=list)
    superseded: list = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(
            self.created or self.reactivated or self.updated
            or self.deactivated or self.superseded
        )

    def __str__(self):
        return (
            f'{len(self.created)} creadas, {len(self.reactivated)} reactivadas, '
            f'{len(self.updated)} actualizadas, {len(self.deactivated)} desactivadas, '
            f'{len(self.superseded)} reemplazadas'
        )


def derive_alerts(response, *, rules=None) -> DerivationResult:
    """Aplica las reglas al snapshot de `response` y sincroniza sus alertas.

    Es el único punto de entrada: lo llama el alta de una respuesta
    (`QuestionnaireResponse.record`) y lo llamará igual la vía del agente por
    n8n cuando exista. No hay señal detrás precisamente para que ese camino sea
    explícito y se pueda invocar a mano sobre una respuesta ya guardada.
    """
    result = DerivationResult()
    specs = {spec.alert_type: spec for spec in evaluate_snapshot(response.snapshot, rules)}

    with transaction.atomic():
        result.superseded = _supersede_previous_responses(response)

        existing = {
            alert.alert_type: alert
            for alert in ClinicalAlert.all_objects.filter(
                patient_id=response.patient_id,
                source=ClinicalAlert.Source.DERIVED,
                source_response=response,
            )
        }

        for alert_type, spec in specs.items():
            alert = existing.get(alert_type)
            if alert is None:
                result.created.append(_create_alert(response, spec))
            else:
                _refresh_alert(alert, spec, result)

        # Lo que esta misma respuesta pedía antes y ya no: una corrección sobre
        # el mismo cuestionario contestado (raro, pero es el caso que el
        # enunciado llama «corrección»).
        for alert_type, alert in existing.items():
            if alert_type not in specs and alert.is_active:
                alert.deactivate()
                result.deactivated.append(alert)

    return result


def _create_alert(response, spec) -> ClinicalAlert:
    return ClinicalAlert.objects.create(
        patient_id=response.patient_id,
        alert_type=spec.alert_type,
        severity=spec.severity,
        note=spec.note,
        source=ClinicalAlert.Source.DERIVED,
        source_response=response,
        # Nadie la levantó: la levantó una regla. `created_by` se queda vacío y
        # el «quién» queda en el ChangeLog como acción del sistema.
        created_by=None,
    )


def _refresh_alert(alert, spec, result: DerivationResult) -> None:
    """Pone al día una alerta ya derivada de esta misma respuesta."""
    changed_fields = []

    if alert.note != spec.note:
        alert.note = spec.note
        changed_fields.append('note')
    if alert.severity != spec.severity:
        alert.severity = spec.severity
        changed_fields.append('severity')

    was_inactive = not alert.is_active
    if was_inactive:
        # La condición vuelve a sostenerse: se reactiva la misma fila en vez de
        # crear una segunda, para no partir en dos la historia del aviso.
        alert.is_active = True
        changed_fields.append('is_active')

    if not changed_fields:
        return

    alert.save(update_fields=[*changed_fields, 'updated_at'])
    (result.reactivated if was_inactive else result.updated).append(alert)


def _supersede_previous_responses(response) -> list:
    """Apaga las alertas derivadas de respuestas anteriores del mismo cuestionario.

    «Anterior» se mide por `filled_at`, no por el id: lo que manda es cuándo se
    contestó. Una respuesta posterior que se derive tarde no puede quedar
    apagada por una anterior que se derive después.
    """
    previous = ClinicalAlert.objects.filter(
        patient_id=response.patient_id,
        source=ClinicalAlert.Source.DERIVED,
        is_active=True,
        source_response__version__template_id=response.version.template_id,
        source_response__filled_at__lte=response.filled_at,
    ).exclude(source_response_id=response.pk)

    superseded = []
    for alert in previous:
        # Una a una: los bulk se saltan la auditoría, y apagar un aviso clínico
        # no puede quedar sin registrar.
        alert.deactivate()
        superseded.append(alert)
    return superseded
