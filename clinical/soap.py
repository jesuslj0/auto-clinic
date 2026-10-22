"""Etiquetas de la nota clínica: SOAP presentado en lenguaje de podología.

El modelo (`ClinicalNote`) es SOAP y sigue siéndolo; lo que vive aquí es **cómo se
le presenta al profesional**: nombre de cada apartado, la letra SOAP como
referencia discreta, qué va en cada caja y un ejemplo. Formulario, ficha y detalle
de la nota leen de aquí, para que las tres pantallas no puedan llamar distinto al
mismo campo. Cambiar una etiqueta es tocar este fichero; no hay migración porque
las etiquetas no se guardan.

Cinco apartados, en el orden en que se leen:

- **Antecedentes** — calzado, actividad física, patologías. Solo se ofrece en la
  primera nota del paciente: no se repite en cada revisión.
- **Motivo de consulta** (S) — un campo.
- **Exploración** (O) — cuatro subcampos opcionales: inspección, palpación,
  biomecánica y pisada, pruebas complementarias. Es la parte con más contenido.
- **Diagnóstico** (A) — un campo.
- **Tratamiento y plan** (P) — dos subcampos: lo hecho hoy, y lo que queda
  pendiente (indicaciones y seguimiento).

`objective` y `plan` fueron antes los campos únicos de Exploración y Plan. Se
conservan como **texto anterior** (`legacy`): una nota escrita así se sigue leyendo
y un borrador antiguo se sigue pudiendo editar, pero las notas nuevas no los usan.

Módulo puro: sin base de datos ni formularios.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NoteField:
    """Un campo de texto de la nota y cómo se le presenta."""

    field: str          # nombre del campo en `ClinicalNote`
    label: str          # etiqueta; vacía = se llama como su apartado
    hint: str           # qué va aquí
    example: str        # ejemplo de podología (también sirve de placeholder)
    rows: int = 3


@dataclass(frozen=True)
class NoteSection:
    """Un apartado de la nota: uno o varios campos bajo un mismo nombre."""

    key: str
    title: str
    letter: str                      # S / O / A / P; vacío si no es SOAP
    hint: str                        # explicación del apartado entero
    fields: tuple[NoteField, ...]
    legacy: NoteField | None = None  # campo único anterior a los subcampos

    @property
    def letter_name(self) -> str:
        """Qué significa la letra SOAP, para su `title` (la referencia discreta)."""
        return LETTER_NAMES.get(self.letter, '')


#: El nombre de cada letra SOAP: lo que lee quien pasa el ratón por la etiqueta.
LETTER_NAMES = {
    'S': 'S de SOAP: Subjetivo',
    'O': 'O de SOAP: Objetivo',
    'A': 'A de SOAP: Análisis (Assessment)',
    'P': 'P de SOAP: Plan',
}


BACKGROUND = NoteSection(
    key='background',
    title='Antecedentes',
    letter='',
    hint=(
        'Calzado habitual, actividad física y patologías relevantes (diabetes, circulación, '
        'alergias, medicación). Solo se anota en la primera visita; en las revisiones no hace falta repetirlo.'
    ),
    fields=(NoteField(
        field='background',
        label='',
        hint='',
        example='Calzado deportivo a diario. Corre tres veces por semana. Diabetes tipo 2 desde hace 8 años.',
        rows=4,
    ),),
)

REASON = NoteSection(
    key='reason',
    title='Motivo de consulta',
    letter='S',
    hint=(
        'Lo que cuenta el paciente con sus palabras: motivo de consulta, síntomas, desde cuándo, '
        'qué lo empeora o lo mejora y medicación.'
    ),
    fields=(NoteField(
        field='subjective',
        label='',
        hint='',
        example='Dolor en el talón derecho al levantarse por la mañana desde hace 3 semanas.',
        rows=4,
    ),),
)

EXAM = NoteSection(
    key='exam',
    title='Exploración',
    letter='O',
    hint='Lo que el profesional observa y mide. Todos los apartados son opcionales: rellena los que apliquen.',
    fields=(
        NoteField(
            field='inspection',
            label='Inspección',
            hint='Lo que se ve: piel, uñas, deformidades, callosidades, signos de inflamación.',
            example='Hiperqueratosis bajo la cabeza del 2.º metatarsiano derecho. Uñas sin alteraciones.',
        ),
        NoteField(
            field='palpation',
            label='Palpación',
            hint='Dolor, temperatura, pulsos y sensibilidad.',
            example='Dolor a la palpación en la inserción medial de la fascia plantar. Pulsos pedios presentes.',
        ),
        NoteField(
            field='biomechanics',
            label='Biomecánica y pisada',
            hint='Tipo de pie, alineación, rangos de movimiento y análisis de la marcha o la pisada.',
            example='Pie pronado. Dorsiflexión de tobillo limitada. Apoyo medial aumentado al caminar.',
        ),
        NoteField(
            field='complementary_tests',
            label='Pruebas complementarias',
            hint='Mediciones, test específicos y resultados de imagen.',
            example='Ecografía: engrosamiento de la fascia plantar de 5 mm.',
        ),
    ),
    legacy=NoteField(
        field='objective',
        label='Exploración (texto anterior)',
        hint='Texto de la exploración escrito antes de existir los subcampos.',
        example='',
        rows=4,
    ),
)

DIAGNOSIS = NoteSection(
    key='diagnosis',
    title='Diagnóstico',
    letter='A',
    hint=(
        'La interpretación clínica a partir de lo anterior: diagnóstico o diagnósticos diferenciales '
        'y cómo evoluciona respecto a visitas anteriores.'
    ),
    fields=(NoteField(
        field='assessment',
        label='',
        hint='',
        example=(
            'Fascitis plantar derecha, probablemente asociada a la pronación y al acortamiento '
            'de la musculatura posterior.'
        ),
        rows=4,
    ),),
)

TREATMENT = NoteSection(
    key='treatment',
    title='Tratamiento y plan',
    letter='P',
    hint='Separa lo que se hizo en esta sesión de lo que queda pendiente: así es fácil preparar la próxima cita.',
    fields=(
        NoteField(
            field='treatment_done',
            label='Tratamiento realizado hoy',
            hint='Lo que se ha hecho en esta sesión.',
            example='Quiropodia con deslaminado de la hiperqueratosis. Vendaje funcional.',
        ),
        NoteField(
            field='follow_up',
            label='Indicaciones y seguimiento',
            hint='Indicaciones al paciente, pruebas o derivaciones que se piden y cuándo es la próxima revisión.',
            example='Estiramientos diarios y plantillas a medida. Revisión en 4 semanas.',
        ),
    ),
    legacy=NoteField(
        field='plan',
        label='Tratamiento y plan (texto anterior)',
        hint='Texto del plan escrito antes de existir los subcampos.',
        example='',
        rows=4,
    ),
)

#: Los apartados, en el orden en que se leen.
SECTIONS: tuple[NoteSection, ...] = (BACKGROUND, REASON, EXAM, DIAGNOSIS, TREATMENT)

#: Cada campo de contenido de la nota, por nombre.
FIELD_META: dict[str, NoteField] = {
    field.field: field
    for section in SECTIONS
    for field in (*section.fields, *([section.legacy] if section.legacy else []))
}

#: Nombres de todos los campos de contenido, en el orden de lectura.
FIELDS: tuple[str, ...] = tuple(FIELD_META)

#: Los campos de texto anterior a los subcampos.
LEGACY_FIELDS: tuple[str, ...] = tuple(
    section.legacy.field for section in SECTIONS if section.legacy
)


def _value(note, name) -> str:
    return (getattr(note, name, '') or '').strip()


def sections_for(note) -> list[dict]:
    """Los apartados de una nota para leerla, con el texto de cada campo.

    Solo se enseñan los campos que dicen algo: una exploración con tres de sus
    cuatro subcampos vacíos no pinta tres «Sin anotar». Un apartado sin ningún
    campo relleno se devuelve vacío (`filled` falso), salvo Antecedentes, que
    directamente no aparece si no se anotó. El texto anterior (`legacy`) sale
    como un campo más, con su etiqueta, cuando lo hay.
    """
    result = []
    for section in SECTIONS:
        names = [field.field for field in section.fields]
        if section.legacy:
            names.append(section.legacy.field)
        filled = [
            {
                'field': name,
                'label': FIELD_META[name].label,
                'value': getattr(note, name, '') or '',
            }
            for name in names
            if _value(note, name)
        ]
        if section is BACKGROUND and not filled:
            continue
        result.append({
            'key': section.key,
            'title': section.title,
            'letter': section.letter,
            'letter_name': section.letter_name,
            'fields': filled,
            'filled': bool(filled),
        })
    return result


def preview_for(note, limit=140) -> str:
    """Una línea para listar la nota: el diagnóstico, o lo primero que haya."""
    for name in ('assessment', 'subjective', 'treatment_done', 'follow_up',
                 'inspection', 'palpation', 'biomechanics', 'complementary_tests',
                 'objective', 'plan', 'background'):
        text = _value(note, name)
        if text:
            text = ' '.join(text.split())
            return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'
    return ''


def is_blank(note) -> bool:
    """¿La nota no dice nada en ninguno de sus campos?"""
    return not any(_value(note, name) for name in FIELDS)
