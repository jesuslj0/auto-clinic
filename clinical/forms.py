"""Formularios de la capa clínica.

De momento solo hay uno: el que rellena una anamnesis desde el panel. Es un
formulario **dinámico**, construido en tiempo de ejecución a partir de las
preguntas de una `TemplateVersion` concreta —siempre la vigente—, porque el
cuestionario es dato, no código: cada clínica tiene el suyo y cada versión
tiene las suyas.

Tres decisiones que sostienen el resto:

1. **Los tipos que salen de aquí son los que espera el motor de reglas.** Un
   `Question.AnswerType.BOOLEAN` produce un `True`/`False` de Python, no la
   cadena `'True'`. `clinical/rules.py::is_affirmative` distingue el booleano de
   la cadena, así que guardar el tipo equivocado no daría error: simplemente no
   levantaría la alerta, y el fallo sería silencioso. Por eso se cuida aquí.
2. **Una pregunta booleana obligatoria tiene que poder contestarse «No».** Un
   `BooleanField(required=True)` de Django rechaza `False` —para él «vacío» y
   «no» son lo mismo—, lo que haría imposible la respuesta clínica más frecuente
   de todas. Se usa un `TypedChoiceField` de dos opciones que *coacciona* a
   booleano: el «no» viaja como la cadena `'false'` (que no está vacía, así que
   supera el `required`) y llega a `answers` como `False`.
3. **`is_required` es de la pregunta, no del formulario.** Se respeta campo a
   campo; el formulario no impone obligatoriedad por su cuenta.

Las clases de los widgets se ponen aquí, con los tokens semánticos del tema
(`bg-surface`, `border-line-strong`, `text-content`…), nunca colores literales.
"""
from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS

from clinical.models import Episode, Question

#: Prefijo del nombre de cada campo de pregunta: `q_<id de la pregunta>`.
QUESTION_FIELD_PREFIX = 'q_'

FIELD_CLASS = (
    'block w-full rounded-xl border border-line-strong bg-surface px-4 py-2.5 '
    'text-sm text-content placeholder-content-faint shadow-sm transition '
    'focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-500'
)
#: Mismo control, en estado de error. Se aplica desde `_post_clean()`.
ERROR_FIELD_CLASS = (
    'block w-full rounded-xl border border-danger bg-surface px-4 py-2.5 '
    'text-sm text-content placeholder-content-faint shadow-sm transition '
    'focus:border-transparent focus:outline-none focus:ring-2 focus:ring-danger'
)
RADIO_CLASS = 'h-4 w-4 border-line-strong text-brand-600 focus:ring-brand-500'
CHECKBOX_CLASS = 'h-4 w-4 rounded border-line-strong text-brand-600 focus:ring-brand-500'


def question_field_name(question_id) -> str:
    """Nombre del campo de formulario que corresponde a una pregunta."""
    return f'{QUESTION_FIELD_PREFIX}{question_id}'


def error_id(field_name: str) -> str:
    """`id` del párrafo de error de un campo, para `aria-describedby`."""
    return f'error-{field_name}'


def _json_safe_number(value):
    """Convierte un `Decimal` en algo que `JSONField` sepa serializar.

    El `snapshot` es un `JSONField` sin encoder propio, así que un `Decimal`
    reventaría el `json.dumps` al guardar. Los enteros se guardan como enteros
    («3», no «3.0»): son números de piezas, de años o de intervenciones, y así
    es como se leen luego en la ficha.
    """
    if not isinstance(value, Decimal):
        return value
    if value == value.to_integral_value():
        return int(value)
    return float(value)


class EpisodeChoiceField(forms.ModelChoiceField):
    """Selector de episodio, etiquetado como se lee en la ficha."""

    def label_from_instance(self, obj):
        fecha = obj.opened_at.strftime('%d/%m/%Y')
        motivo = (obj.reason or '').strip().replace('\n', ' ')
        if len(motivo) > 60:
            motivo = f'{motivo[:60]}…'
        return f'Abierto el {fecha} · {motivo}' if motivo else f'Abierto el {fecha}'


class AnamnesisForm(forms.Form):
    """Una anamnesis contestada sobre la versión **vigente** de un cuestionario.

    Recibe la `version` ya resuelta por la vista (nunca la elige: contestar una
    versión antigua sería registrar una anamnesis obsoleta) y el `patient`, que
    es quien acota los episodios ofrecidos.

    No es un `ModelForm` a propósito: el alta de una respuesta va por
    `QuestionnaireResponse.record()`, que es lo que congela el snapshot y dispara
    el motor de alertas. Este formulario valida y devuelve `answers()`; guardar
    es cosa de la vista.
    """

    def __init__(self, *args, version, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        self.patient = patient
        # El `ordering` del modelo ya es (version, order, id): el orden de la
        # pantalla es el de la pregunta, no el de la clave primaria.
        self.questions = list(version.questions.all())

        self._build_episode_fields()
        for question in self.questions:
            self.fields[question_field_name(question.pk)] = self._build_question_field(question)

    # -- episodio -----------------------------------------------------------

    def _build_episode_fields(self):
        """Selector de episodio abierto + motivo para abrir uno nuevo.

        El `queryset` se restringe a los episodios ABIERTOS DE ESTE PACIENTE, y
        no solo para pintar el desplegable: `ModelChoiceField` valida contra él,
        así que colar por POST el id del episodio de otra persona es un error de
        validación y no llega al modelo. `QuestionnaireResponse.clean()` lo
        volvería a rechazar, pero eso es la segunda barrera, no la primera.
        """
        open_episodes = (
            Episode.objects
            .filter(history__patient=self.patient, status=Episode.Status.OPEN)
            .order_by('-opened_at', '-id')
        )
        self.open_episodes = list(open_episodes)
        #: Sin episodios abiertos, el formulario ofrece directamente abrir uno:
        #: es el caso del paciente nuevo, y no puede ser un callejón sin salida.
        self.has_open_episodes = bool(self.open_episodes)

        self.fields['episode'] = EpisodeChoiceField(
            label='Episodio',
            queryset=open_episodes,
            required=False,
            empty_label='Abrir un episodio nuevo…',
            widget=forms.Select(attrs={'class': FIELD_CLASS, 'x-model': 'episodio'}),
            help_text='La anamnesis queda colgada del proceso asistencial al que pertenece.',
        )
        if self.has_open_episodes and not self.is_bound:
            # Se preselecciona el más reciente: es el que está en curso.
            self.fields['episode'].initial = self.open_episodes[0].pk

        self.fields['episode_reason'] = forms.CharField(
            label='Motivo de consulta',
            required=False,  # obligatorio solo si no se elige episodio; ver clean().
            widget=forms.Textarea(attrs={
                'rows': 2,
                'class': FIELD_CLASS,
                'placeholder': 'Ej. Dolor en el talón derecho al caminar',
            }),
        )

    # -- preguntas ----------------------------------------------------------

    def _build_question_field(self, question):
        """El campo que corresponde al tipo de respuesta de la pregunta."""
        common = {
            'label': question.text,       # el enunciado literal, sin retocar
            'required': question.is_required,
        }
        # Las opciones salen del JSON de la pregunta, nunca de una lista fija.
        choices = [(str(option), str(option)) for option in question.options or []]

        if question.answer_type == Question.AnswerType.BOOLEAN:
            # La trampa: `BooleanField(required=True)` rechazaría el «No».
            # `TypedChoiceField` valida sobre la cadena ('true'/'false', ninguna
            # vacía) y coacciona después a un booleano de verdad, que es lo que
            # `rules.is_affirmative()` sabe leer.
            return forms.TypedChoiceField(
                choices=(('true', 'Sí'), ('false', 'No')),
                coerce=lambda value: value == 'true',
                empty_value=None,
                widget=forms.RadioSelect(attrs={'class': RADIO_CLASS}),
                **common,
            )

        if question.answer_type == Question.AnswerType.NUMBER:
            return forms.DecimalField(
                widget=forms.NumberInput(attrs={'class': FIELD_CLASS, 'step': 'any'}),
                **common,
            )

        if question.answer_type == Question.AnswerType.SINGLE_CHOICE:
            return forms.ChoiceField(
                choices=choices,
                widget=forms.RadioSelect(attrs={'class': RADIO_CLASS}),
                **common,
            )

        if question.answer_type == Question.AnswerType.MULTIPLE_CHOICE:
            return forms.MultipleChoiceField(
                choices=choices,
                widget=forms.CheckboxSelectMultiple(attrs={'class': CHECKBOX_CLASS}),
                **common,
            )

        return forms.CharField(
            widget=forms.Textarea(attrs={
                'rows': 3,
                'class': f'{FIELD_CLASS} resize-none',
                'placeholder': 'Escribe la respuesta…',
            }),
            **common,
        )

    # -- presentación -------------------------------------------------------

    @property
    def question_rows(self):
        """`(pregunta, campo)` en el orden del cuestionario, para la plantilla.

        La plantilla decide el marcado por `question.answer_type`; el formulario
        no devuelve HTML.
        """
        for question in self.questions:
            yield {
                'question': question,
                'field': self[question_field_name(question.pk)],
                'error_id': error_id(question_field_name(question.pk)),
            }

    # -- validación ---------------------------------------------------------

    def clean(self):
        cleaned = super().clean()
        # Sin episodio elegido, lo que se pide es abrir uno, y un episodio sin
        # motivo de consulta no es un episodio.
        if not cleaned.get('episode') and not (cleaned.get('episode_reason') or '').strip():
            self.add_error(
                'episode_reason',
                'Indica el motivo de consulta para abrir un episodio nuevo.',
            )
        return cleaned

    def _post_clean(self):
        """Marca los campos con error, para el tema y para los lectores de pantalla.

        Se hace aquí y no en `__init__` porque los errores no existen hasta que
        el formulario se valida; `_post_clean()` es el último paso de
        `full_clean()`, con `_errors` ya poblado.
        """
        super()._post_clean()
        for name in self._errors:
            if name == NON_FIELD_ERRORS or name not in self.fields:
                continue
            field = self.fields[name]
            widget = field.widget
            # En radios y casillas el aviso va en el `<fieldset>` que pone la
            # plantilla: describir además cada input repetiría el mismo error una
            # vez por opción. (El `aria-invalid` de cada input lo pone Django).
            if isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            # El error se SUMA a lo que ya describe al control. Django apunta el
            # texto de ayuda con `aria-describedby` al renderizar, pero se calla
            # si el widget ya trae el atributo: si aquí lo sustituyéramos sin
            # más, un campo con ayuda la perdería justo cuando falla.
            described = [value for value in [widget.attrs.get('aria-describedby')] if value]
            if field.help_text and not described:
                described.append(f'{self[name].auto_id}_helptext')
            described.append(error_id(name))
            widget.attrs['aria-describedby'] = ' '.join(described)
            widget.attrs['aria-invalid'] = 'true'
            if widget.attrs.get('class', '').startswith(FIELD_CLASS):
                widget.attrs['class'] = widget.attrs['class'].replace(
                    FIELD_CLASS, ERROR_FIELD_CLASS, 1
                )

    # -- salida -------------------------------------------------------------

    def answers(self) -> dict:
        """`{question_id: respuesta}` con los tipos que espera el motor de reglas.

        - `boolean` → `True` / `False` (booleanos de Python, no cadenas).
        - `number` → `int` o `float` (nunca `Decimal`: el snapshot es JSON).
        - `single_choice` → la opción, tal cual está escrita en `options`.
        - `multiple_choice` → lista de opciones.
        - `text` → la cadena.

        Lo que no se contestó se omite: `build_response_snapshot()` deja esas
        entradas con `answer: None`, que es «sin responder». Ojo: `False` y `0`
        SÍ son respuestas y se guardan.
        """
        answers = {}
        for question in self.questions:
            value = self.cleaned_data.get(question_field_name(question.pk))
            if value is None or value == '' or value == []:
                continue
            answers[question.pk] = _json_safe_number(value)
        return answers
