"""Congelado literal de un cuestionario en el momento de responderlo.

Una respuesta de anamnesis NO puede ser un puñado de FK a `Question`: si mañana
se corrige la redacción de una pregunta, se retira una opción o se despublica la
versión entera, la respuesta guardada pasaría a decir algo que el paciente nunca
contestó. Eso es exactamente lo que la historia clínica no puede permitirse
(Ley 41/2002: el registro debe reflejar lo que se documentó *entonces*).

Por eso se guarda una **copia literal**: el texto de cada pregunta, su tipo, sus
opciones y la respuesta dada, todo dentro del JSON de la propia respuesta. La FK
a `TemplateVersion` se conserva para saber de qué documento salió, pero no se
necesita para leer la respuesta: el snapshot se basta solo.
"""
from django.core.exceptions import ValidationError

# Claves de cada entrada del snapshot. Se declaran aquí porque son el contrato
# de lectura de toda respuesta ya guardada: renombrar una de estas claves rompe
# el histórico, no solo el código nuevo. Añadir una sí es seguro: las respuestas
# antiguas simplemente no la traen, y quien lea debe usar `entry.get(...)`.
ENTRY_KEYS = (
    'question_id',
    'code',
    'order',
    'text',
    'answer_type',
    'required',
    'options',
    'answer',
)


def normalize_answers(answers) -> dict:
    """Normaliza las claves de `answers` a `str(question_id)`.

    Las respuestas pueden llegar del ORM (claves `int`), de un formulario o de un
    JSON de n8n (claves `str`). El snapshot se indexa siempre por cadena, que es
    lo único que sobrevive a un viaje por JSON.
    """
    if not answers:
        return {}
    return {str(key): value for key, value in answers.items()}


def build_response_snapshot(version, answers=None) -> list[dict]:
    """Copia literal de las preguntas de `version` junto a las respuestas dadas.

    `answers` es un dict `{question_id: respuesta}`; las preguntas sin respuesta
    quedan con `answer=None`. Una clave que no corresponda a ninguna pregunta de
    la versión es un error (respuesta cruzada de otro cuestionario), no un valor
    a ignorar en silencio.
    """
    normalized = normalize_answers(answers)

    questions = list(version.questions.all())
    known_ids = {str(question.pk) for question in questions}

    unknown = sorted(set(normalized) - known_ids)
    if unknown:
        raise ValidationError(
            f'Respuestas para preguntas que no pertenecen a la versión '
            f'#{version.pk}: {unknown}.'
        )

    return [
        {
            'question_id': question.pk,
            # El código viaja DENTRO del snapshot, no se resuelve después por la
            # FK: es lo que permite que el motor de alertas lea una respuesta de
            # hace años sin depender de la pregunta viva.
            'code': question.code,
            'order': question.order,
            'text': question.text,
            'answer_type': question.answer_type,
            'required': question.is_required,
            # `list()` y no la referencia: el snapshot no debe compartir objeto
            # con la pregunta viva.
            'options': list(question.options or []),
            'answer': normalized.get(str(question.pk)),
        }
        for question in questions
    ]


def is_answered(entry) -> bool:
    """`True` si la entrada del snapshot tiene una respuesta con contenido.

    `False` y `0` SÍ son respuestas («no» y «cero» son datos clínicos); lo que no
    cuenta es `None`, la cadena vacía y la lista vacía.
    """
    answer = entry.get('answer')
    if answer is None:
        return False
    if isinstance(answer, (str, list, dict)):
        return len(answer) > 0
    return True
