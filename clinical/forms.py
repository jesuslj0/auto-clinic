"""Formularios de la capa clínica.

Son cuatro: el que rellena una anamnesis, el que registra una lesión sobre el
mapa del pie, el que anota cómo está esa lesión en una visita
(`LesionObservationForm`) y el que la da por resuelta (`LesionResolveForm`). Los
dos primeros cuelgan lo que registran de un episodio y comparten para eso
`EpisodeSelectionMixin`; los dos últimos ya lo tienen resuelto, porque la lesión
sobre la que trabajan trae el suyo.

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

from clinical.files import ALLOWED_IMAGE_TYPES, validate_clinical_image
from clinical.models import Episode, Lesion, LesionObservation, Question, Visit

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
#: Selector de ficheros. El botón interno se estiliza aparte (`file:`) porque el
#: navegador lo pinta con su propio control y no hereda del contenedor.
FILE_CLASS = (
    'block w-full cursor-pointer rounded-xl border border-line-strong bg-surface '
    'text-sm text-content-subtle shadow-sm transition file:mr-3 file:cursor-pointer '
    'file:rounded-l-xl file:border-0 file:bg-muted-strong file:px-4 file:py-2.5 '
    'file:text-sm file:font-semibold file:text-content-muted '
    'focus:outline-none focus:ring-2 focus:ring-brand-500'
)


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


# ---------------------------------------------------------------------------
# Seguimiento de la lesión: observaciones y cierre
# ---------------------------------------------------------------------------


class VisitChoiceField(forms.ModelChoiceField):
    """Selector de visita, etiquetado como se lee en la ficha."""

    def label_from_instance(self, obj):
        momento = timezone.localtime(obj.occurred_at).strftime('%d/%m/%Y %H:%M')
        return f'{momento} · {obj.professional}'


class MultipleFileInput(forms.ClearableFileInput):
    """`<input type="file" multiple>`. Django exige declararlo así."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Varios ficheros en un solo campo; `clean()` devuelve siempre una lista.

    Es el patrón que documenta Django: un `FileField` normal se queda con el
    último fichero del `multiple` y descartaría el resto en silencio, que es el
    peor modo posible de fallar — la foto se selecciona, no da error y no está.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data if item]
        return [clean_one(data, initial)] if data else []


class LesionObservationForm(ErrorHighlightMixin, forms.ModelForm):
    """Cómo está la lesión hoy: las medidas de esta visita y su descripción.

    Cuatro decisiones:

    1. **La visita es del episodio de la lesión, y no se elige libremente.** El
       modelo lo exige (`LesionObservation._validate_visit`), pero eso sería una
       excepción; aquí el `queryset` del campo ya está acotado a las visitas de
       ese episodio, así que colar por POST la visita de otro proceso —o de otro
       paciente— es un error de validación y no llega al modelo.

    2. **Sin visita elegida, se registra una.** Observar una lesión ES un
       encuentro clínico, y el caso normal en la consulta es «la estoy viendo
       ahora»: obligar a crear antes la visita por otro camino dejaría el
       seguimiento a medias. Es el mismo patrón que el episodio en
       `EpisodeSelectionMixin`: se elige uno o se abre. Quien la crea es la
       vista; aquí solo se valida que se puede y con quién.

    3. **Un episodio cerrado no admite visitas nuevas** (`Visit.save` lanza
       `EpisodeClosed`). Si la lesión cuelga de uno cerrado, la visita pasa a ser
       obligatoria: se anota sobre una de las que ya hubo, o no se anota. Reabrir
       el episodio es otra decisión, y se toma en otro sitio.

    4. **Las medidas son opcionales pero nunca negativas.** No toda lesión se
       mide —una hiperqueratosis se describe—, y por eso ninguna es obligatoria;
       lo que no puede entrar es un número imposible, que además rompería el
       `CheckConstraint` de la tabla.

    5. **Las fotos se validan aquí *además* de en el modelo.** `LesionAttachment`
       las examina por contenido en su `save()` y eso es lo que de verdad protege
       la entrada (vale igual para el adjunto que llegue por la vía del agente);
       repetir la comprobación en el formulario no la sustituye, sirve para que
       un PDF renombrado a `.jpg` sea un error de campo legible y no una
       excepción a media transacción. Lo que se sube va al **bucket privado** con
       clave UUID; aquí nunca se toca una URL.
    """

    #: Con qué precisión se piden las medidas. Décimas de milímetro: es lo que
    #: resuelve una regla milimetrada, y el modelo guarda un decimal.
    MEASUREMENT_STEP = '0.1'

    #: Cuántas fotos caben en una observación. No es una regla clínica: es un
    #: tope de sentido común para que un dedazo en el selector de archivos no
    #: mande cien imágenes al bucket en una sola petición.
    MAX_PHOTOS = 8

    photos = MultipleFileField(
        label='Fotografías',
        required=False,
        help_text='JPEG, PNG o WebP. Se guardan en el almacén clínico privado y '
                  'solo se sirven con enlaces firmados que caducan.',
    )

    visit = VisitChoiceField(
        label='Visita',
        queryset=Visit.objects.none(),   # lo acota `__init__` al episodio
        required=False,
        empty_label='Registrar la visita de hoy…',
        widget=forms.Select(attrs={'class': FIELD_CLASS, 'x-model': 'visita'}),
        help_text='El encuentro en el que se observó la lesión.',
    )

    class Meta:
        model = LesionObservation
        fields = ('observed_at', 'length_mm', 'width_mm', 'depth_mm', 'description')
        labels = {
            'observed_at': 'Fecha de la observación',
            'length_mm': 'Largo (mm)',
            'width_mm': 'Ancho (mm)',
            'depth_mm': 'Profundidad (mm)',
            'description': 'Descripción clínica',
        }
        widgets = {
            # `format` explícito por lo mismo que en `LesionForm`: en español el
            # formato de entrada por defecto es dd/mm/aaaa y un `<input
            # type="date">` solo entiende aaaa-mm-dd.
            'observed_at': forms.DateInput(
                attrs={'type': 'date', 'class': FIELD_CLASS}, format='%Y-%m-%d',
            ),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': f'{FIELD_CLASS} resize-none',
                'placeholder': 'Aspecto del lecho y de los bordes, exudado, signos de '
                               'infección, tratamiento aplicado…',
            }),
        }

    def __init__(self, *args, lesion, professional=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lesion = lesion
        self.episode = lesion.episode
        #: Un episodio cerrado no admite visitas nuevas: ver el punto 3.
        self.can_open_visit = self.episode.status == Episode.Status.OPEN
        #: Quién firma la visita que se cree. `None` si quien registra no es
        #: profesional (un administrativo anotando lo que le dictan).
        self.professional = professional

        visits = Visit.objects.filter(episode=self.episode).order_by('-occurred_at', '-id')
        self.visits = list(visits)
        self.fields['visit'].queryset = visits
        if not self.can_open_visit:
            self.fields['visit'].required = True
            self.fields['visit'].empty_label = 'Selecciona la visita…'
            self.fields['visit'].help_text = (
                'El episodio está cerrado y no admite visitas nuevas: la observación '
                'se anota sobre una de las que ya hubo.'
            )
        if self.visits and not self.is_bound:
            # La más reciente: es la que se está atendiendo.
            self.fields['visit'].initial = self.visits[0].pk

        for name in ('length_mm', 'width_mm', 'depth_mm'):
            self.fields[name].widget.attrs.update({
                'class': FIELD_CLASS, 'step': self.MEASUREMENT_STEP, 'min': '0',
                'inputmode': 'decimal',
            })

        # `accept` es una comodidad del selector de archivos, NO un control: lo
        # que decide es `validate_clinical_image`, que mira el contenido.
        self.fields['photos'].widget.attrs.update({
            'class': FILE_CLASS,
            'accept': ','.join(ALLOWED_IMAGE_TYPES),
        })

        if not self.is_bound:
            self.fields['observed_at'].initial = timezone.localdate()

        if self.professional is None and self.can_open_visit:
            # Solo hace falta cuando se va a crear la visita: si se elige una que
            # ya existe, su profesional es el que la atendió y no se toca.
            from appointments.models import Professional

            self.fields['visit_professional'] = forms.ModelChoiceField(
                label='Profesional que atiende',
                queryset=Professional.objects.filter(
                    clinic_id=self.episode.history.clinic_id, is_active=True
                ).select_related('user'),
                required=False,
                empty_label='Selecciona el profesional…',
                widget=forms.Select(attrs={'class': FIELD_CLASS}),
                help_text='Tu usuario no tiene ficha de profesional: indica quién '
                          'realiza la visita que se va a registrar.',
            )

    # -- validación ---------------------------------------------------------

    def clean_observed_at(self):
        observed_at = self.cleaned_data.get('observed_at')
        if observed_at is None:
            return observed_at
        if observed_at > timezone.localdate():
            raise forms.ValidationError('Una observación no se puede fechar en el futuro.')
        if observed_at < self.lesion.detected_at:
            # Antes de detectarla no había nada que observar: casi siempre es un
            # dedazo en el año, y pasaría desapercibido en la serie.
            raise forms.ValidationError(
                'La observación es anterior a la fecha en la que se detectó la lesión '
                f'({self.lesion.detected_at:%d/%m/%Y}).'
            )
        return observed_at

    def clean_photos(self):
        """Cada fichero se mira por dentro antes de aceptarlo.

        El mensaje se da **por fichero y con su nombre**: al subir cinco fotos de
        golpe, «una no es una imagen» no dice cuál. La comprobación es la misma
        que hará el modelo al guardar (`validate_clinical_image`), aquí solo para
        que el rechazo sea un error de campo y no una excepción.
        """
        photos = self.cleaned_data.get('photos') or []
        if len(photos) > self.MAX_PHOTOS:
            raise forms.ValidationError(
                f'Se pueden adjuntar como mucho {self.MAX_PHOTOS} fotografías por '
                f'observación; has seleccionado {len(photos)}.'
            )

        errors = []
        for photo in photos:
            try:
                # `forms.ValidationError` es la misma clase que la de
                # `django.core.exceptions`, que es la que lanza el validador.
                validate_clinical_image(photo)
            except forms.ValidationError as exc:
                errors.extend(f'«{photo.name}»: {message}' for message in exc.messages)
        if errors:
            raise forms.ValidationError(errors)
        return photos

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('visit') is not None:
            return cleaned
        if not self.can_open_visit:
            # `required=True` ya lo habrá señalado; este es el caso de un POST
            # que llega con el campo vacío contra un episodio recién cerrado.
            self.add_error('visit', 'El episodio está cerrado: elige una visita ya registrada.')
        elif self.professional is None and cleaned.get('visit_professional') is None:
            self.add_error(
                'visit_professional',
                'Indica qué profesional realiza la visita, o elige una visita ya registrada.',
            )
        return cleaned

    # -- presentación -------------------------------------------------------

    @property
    def measurement_fields(self):
        """Las tres medidas en su orden, para pintarlas juntas.

        Van en una sola rejilla porque se leen juntas: «14 × 9,5 × 3 mm» es un
        dato, no tres. Que el orden lo diga el formulario y no la plantilla evita
        que una y otra acaben discrepando.
        """
        return [self['length_mm'], self['width_mm'], self['depth_mm']]

    # -- salida -------------------------------------------------------------

    def new_visit_professional(self):
        """Quién firma la visita que hay que crear: el usuario, o el elegido.

        No se llama `visit_professional` a propósito: ese nombre es el del campo,
        y un método homónimo lo taparía en cuanto alguien lo pintara con
        `{{ form.visit_professional }}`.
        """
        return self.professional or self.cleaned_data.get('visit_professional')

    def visit_moment(self):
        """Cuándo ocurrió la visita que hay que crear.

        `Visit.occurred_at` es un instante y la observación solo trae el día. Se
        combinan: la fecha es la que dice el profesional y la hora, la de ahora.
        Fechar la visita de una observación retrospectiva a las 00:00 la
        colocaría antes que todo lo demás de ese día en la ficha.
        """
        from datetime import datetime

        observed_at = self.cleaned_data['observed_at']
        now = timezone.localtime()
        if observed_at == now.date():
            return now
        return timezone.make_aware(
            datetime.combine(observed_at, now.time()), now.tzinfo
        )


class LesionResolveForm(ErrorHighlightMixin, forms.Form):
    """Dar de alta la lesión, con su fecha.

    No es un `ModelForm`: el cierre va por `Lesion.resolve(on=…)`, que es lo que
    mantiene coherentes el estado y la fecha (el `CheckConstraint` de la tabla
    exige que «resuelta» venga siempre con la suya). Aquí solo se valida la
    fecha, que es lo único que pone quien cierra.
    """

    resolved_at = forms.DateField(
        label='Fecha de resolución',
        widget=forms.DateInput(
            attrs={'type': 'date', 'class': FIELD_CLASS}, format='%Y-%m-%d',
        ),
        help_text='El día en que la lesión quedó cerrada.',
    )

    def __init__(self, *args, lesion, **kwargs):
        super().__init__(*args, **kwargs)
        self.lesion = lesion
        if not self.is_bound:
            self.fields['resolved_at'].initial = timezone.localdate()

    def clean_resolved_at(self):
        resolved_at = self.cleaned_data['resolved_at']
        if resolved_at > timezone.localdate():
            raise forms.ValidationError('Una lesión no se puede resolver en el futuro.')
        if resolved_at < self.lesion.detected_at:
            raise forms.ValidationError(
                'La lesión no puede resolverse antes de detectarse '
                f'({self.lesion.detected_at:%d/%m/%Y}).'
            )
        return resolved_at
