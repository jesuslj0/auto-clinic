"""Reglas anamnesis → alerta, y su evaluación pura.

Este módulo **no toca la base de datos**. Recibe el snapshot de una respuesta
—una lista de diccionarios— y devuelve qué alertas pide ese snapshot. Quién las
crea, las reactiva o las apaga es cosa de `clinical/derivation.py`.

Dos decisiones que sostienen todo lo demás:

- **Las reglas son datos, no ramas `if`.** Cada `AlertRule` es una fila del
  registro `PODIATRY_RULES`. Añadir una regla nueva es añadir un elemento a la
  lista; el motor no se toca. Y como son datos, se pueden pasar a mano en un
  test sin montar nada.
- **Se casa por `code`, nunca por el texto ni por el orden.** El enunciado de una
  pregunta se corrige, se traduce y se reordena; su código no. Por eso
  `Question.code` viaja congelado dentro del snapshot: una respuesta de hace
  años se sigue interpretando aunque la pregunta viva haya cambiado de redacción
  o ya no exista.

Una entrada del snapshot sin `code` (las anteriores a que existiera el campo)
no casa con ninguna regla y se ignora sin ruido.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

from clinical.models import ClinicalAlert

# Formas de decir «sí» que pueden llegar de un formulario web, de n8n o de un
# desplegable. El booleano de verdad es lo normal; esto es la red por si la
# respuesta viaja como texto.
AFFIRMATIVE_ANSWERS = frozenset({'si', 'sí', 'yes', 'true', 'verdadero', '1'})


def is_affirmative(answer) -> bool:
    """`True` si la respuesta significa que sí.

    Deliberadamente estricto con los números: `0` es «no» y cualquier otro
    número NO se interpreta como «sí» (una pregunta numérica no es un sí/no).
    """
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, str):
        return answer.strip().lower() in AFFIRMATIVE_ANSWERS
    return False


def answer_in(*values) -> Callable[[object], bool]:
    """Condición para preguntas de elección: la respuesta está entre `values`.

    Compara sin distinguir mayúsculas ni espacios sobrantes, y sirve tanto para
    opción única (una cadena) como para opción múltiple (una lista).
    """
    wanted = {str(value).strip().lower() for value in values}

    def matcher(answer) -> bool:
        if answer is None:
            return False
        given = answer if isinstance(answer, (list, tuple, set)) else [answer]
        return any(str(item).strip().lower() in wanted for item in given)

    return matcher


@dataclass(frozen=True)
class AlertSpec:
    """Alerta que un snapshot pide. Todavía no es una fila: es una intención."""

    alert_type: str
    severity: str
    note: str
    question_code: str


@dataclass(frozen=True)
class AlertRule:
    """Una condición sobre una pregunta y la alerta que levanta si se cumple."""

    question_code: str
    alert_type: str
    severity: str = ClinicalAlert.Severity.CRITICAL
    # `{question}` y `{answer}` se sustituyen con lo que diga el snapshot, que es
    # el texto literal que se le mostró a quien contestó.
    note_template: str = 'Derivada de la anamnesis — «{question}»: {answer}'
    when: Callable[[object], bool] = is_affirmative

    def matches(self, entry: dict) -> bool:
        code = entry.get('code')
        return bool(code) and code == self.question_code and self.when(entry.get('answer'))

    def build_spec(self, entry: dict) -> AlertSpec:
        return AlertSpec(
            alert_type=self.alert_type,
            severity=self.severity,
            note=self.note_template.format(
                question=entry.get('text', ''),
                answer=_render_answer(entry.get('answer')),
            ),
            question_code=self.question_code,
        )


def _render_answer(answer) -> str:
    if isinstance(answer, bool):
        return 'sí' if answer else 'no'
    if isinstance(answer, (list, tuple, set)):
        return ', '.join(str(item) for item in answer)
    if answer is None:
        return '—'
    return str(answer)


# ---------------------------------------------------------------------------
# Registro de reglas
# ---------------------------------------------------------------------------
#
# Las críticas de podología: las que cambian el tratamiento o lo contraindican.
# Para añadir una regla se añade una fila aquí y se le pone el `code` a la
# pregunta correspondiente en la versión del cuestionario. Nada más.

PODIATRY_RULES: tuple[AlertRule, ...] = (
    AlertRule(
        question_code='has_diabetes',
        alert_type=ClinicalAlert.AlertType.DIABETES,
    ),
    AlertRule(
        question_code='has_peripheral_vascular_disease',
        alert_type=ClinicalAlert.AlertType.PERIPHERAL_VASCULAR_DISEASE,
    ),
    AlertRule(
        question_code='has_neuropathy',
        alert_type=ClinicalAlert.AlertType.NEUROPATHY,
    ),
    AlertRule(
        question_code='takes_anticoagulants',
        alert_type=ClinicalAlert.AlertType.ANTICOAGULANTS,
    ),
    AlertRule(
        question_code='allergy_latex',
        alert_type=ClinicalAlert.AlertType.ALLERGY_LATEX,
    ),
    AlertRule(
        question_code='allergy_local_anesthetics',
        alert_type=ClinicalAlert.AlertType.ALLERGY_LOCAL_ANESTHETICS,
    ),
)


def evaluate_snapshot(snapshot, rules: Iterable[AlertRule] | None = None) -> list[AlertSpec]:
    """Qué alertas pide este snapshot. **Función pura: no toca la base de datos.**

    Recorre las entradas en el orden del snapshot y devuelve una `AlertSpec` por
    cada regla que case. Si dos reglas distintas piden el mismo `alert_type`
    —dos preguntas que apuntan a la misma condición—, gana la primera: una
    condición es una alerta, no dos avisos iguales en la ficha.
    """
    rules = PODIATRY_RULES if rules is None else tuple(rules)

    specs: dict[str, AlertSpec] = {}
    for entry in snapshot or []:
        for rule in rules:
            if rule.alert_type in specs:
                continue
            if rule.matches(entry):
                specs[rule.alert_type] = rule.build_spec(entry)
    return list(specs.values())


def with_severity(rule: AlertRule, severity: str) -> AlertRule:
    """Copia de una regla con otra gravedad. Para afinar el registro sin editarlo."""
    return replace(rule, severity=severity)
