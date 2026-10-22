"""Formularios de la ficha desde el móvil del paciente.

Dos familias:

- **Los del paciente** (`PublicAnamnesisForm`, `PublicConsentForm`,
  `IntakeIdentityForm`): son los de siempre (`AnamnesisForm`, `ConsentSignForm`)
  menos la elección de episodio, que en el móvil no pinta nada —la decide la
  clínica al emitir el enlace—. Heredan las validaciones tal cual, así que lo que
  se acepta del paciente es exactamente lo que se acepta de un profesional.
- **El de la clínica** (`PatientFormLinkForm`): qué se pide, y de qué episodio
  colgará.

Lo que viaja en el formulario público no se fía de nada: la versión que se
enseñó va en un campo oculto y, si ya no es la vigente, se rechaza (se firma lo
que se leyó); los campos de episodio no existen, así que no hay id de episodio
ajeno que colar.
"""
from __future__ import annotations

from django import forms

from clinical.forms import (
    CHECKBOX_CLASS,
    FIELD_CLASS,
    AnamnesisForm,
    ConsentSignForm,
    EpisodeSelectionMixin,
    ErrorHighlightMixin,
)
from clinical.intake import is_minor
from clinical.models import ConsentTemplate, QuestionnaireTemplate


class _NoEpisodeMixin:
    """Quita la elección de episodio: en la página pública no la hace el paciente."""

    def build_episode_fields(self, patient):
        self.patient = patient
        self.open_episodes = []
        self.has_open_episodes = False

    def validate_episode_choice(self, cleaned):
        return None


# ---------------------------------------------------------------------------
# Los del paciente
# ---------------------------------------------------------------------------


class PublicAnamnesisForm(_NoEpisodeMixin, AnamnesisForm):
    """La anamnesis vigente, contestada por el paciente."""

    shown_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields['shown_version'].initial = self.version.pk

    def clean_shown_version(self):
        shown = self.cleaned_data.get('shown_version')
        if shown != self.version.pk:
            raise forms.ValidationError(
                'El cuestionario ha cambiado mientras lo rellenabas. '
                'Revisa las preguntas y vuelve a enviarlo.'
            )
        return shown


class PublicConsentForm(_NoEpisodeMixin, ConsentSignForm):
    """El consentimiento vigente, firmado por el paciente (o su representante)."""

    accepted = forms.BooleanField(
        label='He leído y entiendo este documento y doy mi consentimiento.',
        error_messages={'required': 'Marca la casilla para confirmar que has leído el documento y consientes.'},
        widget=forms.CheckboxInput(attrs={'class': CHECKBOX_CLASS}),
    )


class IntakeIdentityForm(ErrorHighlightMixin, forms.Form):
    """Quién contesta: verificación del paciente y, si procede, su representante.

    **Verificación.** Si la ficha tiene fecha de nacimiento, hay que repetirla. Es
    la segunda llave: el enlace sale por WhatsApp y puede acabar en un móvil
    compartido o reenviado. Sin fecha en la ficha no hay nada que comprobar y el
    campo no se pide.

    **Representante.** Un menor de la edad de consentimiento (`INTAKE_MINOR_AGE`)
    no firma por sí mismo, así que ahí nombre y relación son obligatorios y se
    guardan junto a la firma. También se ofrece a cualquier otro paciente (una
    persona mayor a la que ayuda su hija): marcando la casilla, los mismos datos.
    """

    MISMATCH = (
        'Estos datos no coinciden con los de la ficha. '
        'Revisa la fecha de nacimiento del paciente.'
    )

    birth_date = forms.DateField(
        label='Fecha de nacimiento del paciente',
        required=False,
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        widget=forms.DateInput(attrs={'type': 'date', 'class': FIELD_CLASS, 'autocomplete': 'off'}),
        error_messages={'invalid': 'Escribe una fecha válida.'},
    )
    on_behalf = forms.BooleanField(
        label='Estoy rellenando esta ficha en nombre del paciente',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': CHECKBOX_CLASS}),
    )
    signer_name = forms.CharField(
        label='Tu nombre y apellidos',
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'autocomplete': 'name'}),
    )
    signer_relationship = forms.CharField(
        label='Tu relación con el paciente',
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'Madre, padre, tutor legal…'}),
    )

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient
        self.requires_birth_date = patient.date_of_birth is not None
        self.minor = is_minor(patient)
        self.fields['birth_date'].required = self.requires_birth_date
        if self.requires_birth_date:
            self.fields['birth_date'].error_messages['required'] = (
                'Escribe la fecha de nacimiento del paciente.'
            )

    @property
    def behalf_required(self) -> bool:
        return self.minor

    def clean(self):
        cleaned = super().clean()
        birth = cleaned.get('birth_date')
        if self.requires_birth_date and birth is not None and birth != self.patient.date_of_birth:
            # Mismo mensaje y mismo campo que un valor mal escrito: no se dice
            # cuál de los dos datos falló.
            self.add_error('birth_date', self.MISMATCH)
            self.verification_failed = True

        if self.minor or cleaned.get('on_behalf'):
            for name, message in (
                ('signer_name', 'Escribe tu nombre: rellenas la ficha en nombre del paciente.'),
                ('signer_relationship', 'Indica tu relación con el paciente.'),
            ):
                if not (cleaned.get(name) or '').strip():
                    self.add_error(name, message)
        return cleaned

    verification_failed = False

    def signer(self) -> tuple[str, str]:
        """`(nombre, relación)` de quien rellena en nombre del paciente; vacíos si es él."""
        if not (self.minor or self.cleaned_data.get('on_behalf')):
            return '', ''
        return (
            self.cleaned_data['signer_name'].strip(),
            self.cleaned_data['signer_relationship'].strip(),
        )


# ---------------------------------------------------------------------------
# El de la clínica
# ---------------------------------------------------------------------------


class _TemplateChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        version = obj.current_version
        return f'{obj.name} (v{version.number})' if version else obj.name


class PatientFormLinkForm(ErrorHighlightMixin, EpisodeSelectionMixin, forms.Form):
    """Qué se le pide al paciente y de qué episodio colgará.

    Solo se ofrece lo que se podría contestar: cuestionarios y consentimientos
    **activos y con versión vigente publicada**, y siempre de la clínica del
    paciente. `ModelChoiceField` valida contra ese queryset, así que el id de
    una plantilla ajena por POST es un error de validación.
    """

    episode_help_text = (
        'Lo que envíe el paciente queda colgado de este episodio. Si se cierra antes de '
        'que conteste, se abre uno nuevo con el motivo indicado.'
    )
    episode_reason_placeholder = 'Ej. Primera visita'

    questionnaire = _TemplateChoiceField(
        label='Cuestionario de anamnesis', required=False, empty_label='No pedir cuestionario',
        queryset=QuestionnaireTemplate.objects.none(),
        widget=forms.Select(attrs={'class': FIELD_CLASS}),
    )
    consent = _TemplateChoiceField(
        label='Consentimiento a firmar', required=False, empty_label='No pedir firma',
        queryset=ConsentTemplate.objects.none(),
        widget=forms.Select(attrs={'class': FIELD_CLASS}),
    )

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_episode_fields(patient)
        self.fields['questionnaire'].queryset = self._with_current_version(
            QuestionnaireTemplate.objects.filter(clinic_id=patient.clinic_id, is_active=True)
        )
        self.fields['consent'].queryset = self._with_current_version(
            ConsentTemplate.objects.filter(clinic_id=patient.clinic_id, is_active=True)
        )

    @staticmethod
    def _with_current_version(queryset):
        return queryset.filter(
            versions__is_current=True, versions__is_published=True,
            versions__deleted_at__isnull=True,
        ).distinct().order_by('name')

    @property
    def has_choices(self) -> bool:
        return (
            self.fields['questionnaire'].queryset.exists()
            or self.fields['consent'].queryset.exists()
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('questionnaire') and not cleaned.get('consent'):
            raise forms.ValidationError('Elige qué se le pide al paciente: un cuestionario, una firma o ambos.')
        self.validate_episode_choice(cleaned)
        return cleaned
