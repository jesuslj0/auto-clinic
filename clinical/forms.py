"""Formularios de la capa clínica.

Hay dos: el que rellena una anamnesis y el que registra una lesión sobre el mapa
del pie. Los dos cuelgan lo que registran de un episodio y comparten para eso
`EpisodeSelectionMixin`.

El de la anamnesis es un formulario **dinámico**, construido en tiempo de
ejecución a partir de las preguntas de una `TemplateVersion` concreta —siempre la
vigente—, porque el cuestionario es dato, no código: cada clínica tiene el suyo y
cada versión tiene las suyas.

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
from django.utils import timezone

from clinical.models import Episode, Lesion, Question

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


class ErrorHighlightMixin:
    """Marca los campos con error, para el tema y para los lectores de pantalla.

    Se hace en `_post_clean()` y no en `__init__` porque los errores no existen
    hasta que el formulario se valida; `_post_clean()` es el último paso de
    `full_clean()`, con `_errors` ya poblado. En un `ModelForm` es además donde
    se valida la instancia, así que la llamada a `super()` va primero: los
    errores que levante el modelo también se marcan.
    """

    def _post_clean(self):
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
            # Un campo oculto no se puede describir ni marcar: su error se pinta
            # en el aviso general del formulario, que es donde el usuario mira.
            if isinstance(widget, forms.HiddenInput):
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


class EpisodeSelectionMixin:
    """Elegir un episodio abierto del paciente, o abrir uno nuevo con su motivo.

    Todo registro clínico cuelga de un proceso asistencial, y el paciente puede
    tener varios (o ninguno). El patrón, compartido por la anamnesis y por el
    alta de una lesión, es siempre el mismo:

    - Desplegable con los episodios **abiertos de ese paciente**, el más reciente
      preseleccionado, que es el que está en curso.
    - Y, si no elige ninguno, un motivo de consulta con el que abrir uno: un
      paciente nuevo no puede quedarse sin poder registrar nada.

    El `queryset` se restringe al paciente, y no solo para pintar el desplegable:
    `ModelChoiceField` **valida contra él**, así que colar por POST el id del
    episodio de otra persona es un error de validación y no llega al modelo.

    Quien monte el episodio nuevo es la vista (aquí no se escribe en la base de
    datos); el formulario solo garantiza que hay motivo para hacerlo.
    """

    #: Qué cuelga de ese episodio. Lo concreta cada formulario.
    episode_help_text = 'El registro queda colgado del proceso asistencial al que pertenece.'
    episode_reason_placeholder = 'Ej. Dolor en el talón derecho al caminar'

    def build_episode_fields(self, patient):
        """Añade `episode` y `episode_reason`. Se llama desde `__init__`."""
        self.patient = patient
        open_episodes = (
            Episode.objects
            .filter(history__patient=patient, status=Episode.Status.OPEN)
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
            help_text=self.episode_help_text,
        )
        if self.has_open_episodes and not self.is_bound:
            # Se preselecciona el más reciente: es el que está en curso.
            self.fields['episode'].initial = self.open_episodes[0].pk

        self.fields['episode_reason'] = forms.CharField(
            label='Motivo de consulta',
            required=False,  # obligatorio solo si no se elige episodio; ver abajo.
            widget=forms.Textarea(attrs={
                'rows': 2,
                'class': FIELD_CLASS,
                'placeholder': self.episode_reason_placeholder,
            }),
        )

    def validate_episode_choice(self, cleaned):
        """Sin episodio elegido hay que abrir uno, y eso exige un motivo."""
        if not cleaned.get('episode') and not (cleaned.get('episode_reason') or '').strip():
            self.add_error(
                'episode_reason',
                'Indica el motivo de consulta para abrir un episodio nuevo.',
            )


class AnamnesisForm(ErrorHighlightMixin, EpisodeSelectionMixin, forms.Form):
    """Una anamnesis contestada sobre la versión **vigente** de un cuestionario.

    Recibe la `version` ya resuelta por la vista (nunca la elige: contestar una
    versión antigua sería registrar una anamnesis obsoleta) y el `patient`, que
    es quien acota los episodios ofrecidos.

    No es un `ModelForm` a propósito: el alta de una respuesta va por
    `QuestionnaireResponse.record()`, que es lo que congela el snapshot y dispara
    el motor de alertas. Este formulario valida y devuelve `answers()`; guardar
    es cosa de la vista.
    """

    #: `QuestionnaireResponse.clean()` volvería a rechazar un episodio de otro
    #: paciente, pero eso es la segunda barrera, no la primera.
    episode_help_text = 'La anamnesis queda colgada del proceso asistencial al que pertenece.'

    def __init__(self, *args, version, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        # El `ordering` del modelo ya es (version, order, id): el orden de la
        # pantalla es el de la pregunta, no el de la clave primaria.
        self.questions = list(version.questions.all())

        self.build_episode_fields(patient)
        for question in self.questions:
            self.fields[question_field_name(question.pk)] = self._build_question_field(question)

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
        self.validate_episode_choice(cleaned)
        return cleaned

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


# ---------------------------------------------------------------------------
# Alta de una lesión sobre el mapa del pie
# ---------------------------------------------------------------------------


class LesionForm(ErrorHighlightMixin, EpisodeSelectionMixin, forms.ModelForm):
    """Registrar una lesión localizada sobre el mapa del pie.

    Cuatro decisiones, y las cuatro son de seguridad del dato:

    1. **Las coordenadas se revalidan aquí.** `Lesion.save()` y el
       `CheckConstraint` ya las defienden, pero eso convertiría un valor fuera de
       rango en una excepción; en un formulario tiene que ser un error de campo.
       Nunca se confía en el cliente: `x`/`y` llegan de un clic en el navegador y
       se comprueban en el servidor como cualquier otro dato de entrada.

    2. **La zona anatómica se elige a mano, y no se toca el punto.** Son dos
       datos distintos que envejecen distinto (ver `Lesion`): ni se deduce la
       zona de dónde cayó el clic ni se recoloca el marcador según la zona
       elegida.

    3. **El alta es siempre de una lesión ACTIVA.** Una lesión se registra cuando
       se detecta; cerrarla es su ciclo de vida (`Lesion.resolve()`), no su alta,
       y el `CheckConstraint` exige que «resuelta» venga con fecha de resolución.
       Ofrecer aquí «resuelta» obligaría a pedir esa fecha en el alta para
       registrar algo que nadie registra así. `status` se declara igualmente,
       restringido a un único valor, para que un POST manipulado sea un error de
       campo limpio y no una violación de restricción en la base de datos.

    4. **La localización queda congelada** (`Lesion.FROZEN_FIELDS`). Este
       formulario solo crea; no existe editar una lesión para moverla.

    El episodio lo pone la vista (`form.save(commit=False)` y luego se asigna),
    igual que el profesional que la registra: aquí solo se valida cuál se ha
    elegido, o que hay motivo para abrir uno nuevo.
    """

    episode_help_text = 'La lesión queda colgada del proceso asistencial en el que se detecta.'
    episode_reason_placeholder = 'Ej. Úlcera en el antepié derecho'

    #: Único valor admitido en el alta; ver el punto 3 del docstring.
    status = forms.ChoiceField(
        label='Estado',
        choices=[(Lesion.Status.ACTIVE, Lesion.Status.ACTIVE.label)],
        initial=Lesion.Status.ACTIVE,
        widget=forms.HiddenInput(),
        error_messages={
            'invalid_choice': (
                'Una lesión se registra siempre como activa: se cierra después, '
                'con su fecha de resolución.'
            ),
        },
    )

    class Meta:
        model = Lesion
        fields = (
            'laterality', 'view', 'x', 'y',
            'anatomical_zone', 'lesion_type', 'detected_at', 'status',
        )
        labels = {
            'laterality': 'Pie',
            'view': 'Vista',
            'anatomical_zone': 'Zona anatómica',
            'lesion_type': 'Tipo de lesión',
            'detected_at': 'Fecha de detección',
        }
        widgets = {
            # El pie, la vista y el punto no son controles: los fija el mapa
            # (el pie y la vista que se están mirando, y dónde se hizo clic).
            # Viajan ocultos y el panel los enseña escritos, en texto.
            'laterality': forms.HiddenInput(),
            'view': forms.HiddenInput(),
            'x': forms.HiddenInput(),
            'y': forms.HiddenInput(),
            'anatomical_zone': forms.Select(attrs={'class': FIELD_CLASS}),
            'lesion_type': forms.Select(attrs={'class': FIELD_CLASS}),
            # `format` explícito: con el idioma en español el formato de entrada
            # por defecto es dd/mm/aaaa, y un `<input type="date">` solo entiende
            # aaaa-mm-dd (se quedaría en blanco al repintar el formulario).
            'detected_at': forms.DateInput(
                attrs={'type': 'date', 'class': FIELD_CLASS},
                format='%Y-%m-%d',
            ),
        }

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_episode_fields(patient)
        # Elegir zona es un acto clínico: mejor una opción vacía que arrancar
        # con la primera de la lista ya seleccionada sin haberla mirado.
        zone = self.fields['anatomical_zone']
        zone.choices = [('', 'Selecciona la zona…')] + [
            choice for choice in zone.choices if choice[0]
        ]

    # -- validación ---------------------------------------------------------

    def _clean_fraction(self, name):
        """`x`/`y` son fracciones del SVG entre 0 y 1, nunca píxeles."""
        value = self.cleaned_data.get(name)
        if value is None:
            return value
        if not (0.0 <= value <= 1.0):
            raise forms.ValidationError(
                'El punto marcado cae fuera del mapa. Vuelve a marcarlo sobre el dibujo.'
            )
        return value

    def clean_x(self):
        return self._clean_fraction('x')

    def clean_y(self):
        return self._clean_fraction('y')

    def clean_detected_at(self):
        detected_at = self.cleaned_data.get('detected_at')
        if detected_at and detected_at > timezone.localdate():
            raise forms.ValidationError('Una lesión no se puede detectar en el futuro.')
        return detected_at

    def clean(self):
        cleaned = super().clean()
        self.validate_episode_choice(cleaned)
        return cleaned
