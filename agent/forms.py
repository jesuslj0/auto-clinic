import re

from django import forms
from django.utils.text import slugify

from agent.models import AgentProfile, WhatsAppTemplate
from core.models import Clinic


class AgentProfileForm(forms.ModelForm):
    """Identidad y estilo del agente. Lo edita un administrador de la clínica."""

    class Meta:
        model = AgentProfile
        fields = ['agent_name', 'tone', 'address_form', 'emoji_usage', 'welcome_message', 'style_notes']
        widgets = {
            'tone': forms.RadioSelect,
            'address_form': forms.RadioSelect,
            'emoji_usage': forms.RadioSelect,
        }

    def clean_agent_name(self):
        return ' '.join(self.cleaned_data['agent_name'].split())


class MetaCredentialsForm(forms.ModelForm):
    """Credenciales de la Cloud API de WhatsApp de la clínica.

    El token de acceso es un secreto de larga vida: se trata como campo
    write-only (nunca se renderiza) y, si se envía en blanco, se conserva
    el valor guardado en lugar de borrarlo.
    """

    whatsapp_phone_number_id = forms.CharField(
        label='Phone Number ID',
        required=False,
        help_text='Identificador del número que Meta muestra en WhatsApp → API Setup.',
    )
    whatsapp_business_account_id = forms.CharField(
        label='ID de la cuenta de WhatsApp Business',
        required=False,
        help_text='Hace falta para crear y consultar plantillas de mensaje. Está en '
                  'Meta Business → Configuración → Cuentas de WhatsApp.',
    )
    whatsapp_token = forms.CharField(
        label='Token de acceso permanente',
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Se almacena de forma segura y nunca se vuelve a mostrar. '
                  'Déjalo en blanco para conservar el token actual.',
    )

    class Meta:
        model = Clinic
        fields = ['whatsapp_phone_number_id', 'whatsapp_business_account_id', 'whatsapp_token']

    def clean_whatsapp_business_account_id(self):
        # Va dentro de la URL de la Graph API: solo cifras.
        value = self.cleaned_data.get('whatsapp_business_account_id', '').strip()
        if value and not value.isdigit():
            raise forms.ValidationError('El ID de la cuenta son solo números.')
        return value

    def clean_whatsapp_token(self):
        token = self.cleaned_data.get('whatsapp_token', '').strip()
        # Envío en blanco → mantener el token ya almacenado.
        if not token:
            return self.instance.whatsapp_token
        return token


class WebhookForm(forms.ModelForm):
    """Token con el que Meta verifica el webhook. Vive junto a la URL que se pega en Meta."""

    whatsapp_verify_token = forms.CharField(
        label='Token de verificación',
        required=False,
        help_text='Cadena que tú eliges y que debes pegar también en el panel de Meta.',
    )

    class Meta:
        model = Clinic
        fields = ['whatsapp_verify_token']


class WhatsAppTemplateForm(forms.ModelForm):
    """Redacta una plantilla. Las reglas son las que Meta aplica al revisarla.

    Validarlas aquí evita enviar a revisión algo que rebotaría. Los ejemplos de
    las variables llegan como campos `examples` repetidos, uno por `{{n}}`, y se
    guardan junto a la plantilla.
    """

    class Meta:
        model = WhatsAppTemplate
        fields = ['name', 'language', 'category', 'header_text', 'body', 'footer']
        widgets = {'category': forms.RadioSelect}

    def __init__(self, *args, clinic, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic
        # Si Meta ya la conoce, nombre e idioma son su identidad allí.
        if self.instance.pk and self.instance.identity_locked:
            self.fields['name'].disabled = True
            self.fields['language'].disabled = True

    @property
    def examples(self):
        """Ejemplos tal como hay que pintarlos: lo enviado si hay datos, si no lo guardado."""
        if self.is_bound:
            return self.data.getlist('examples')
        return list(self.instance.body_examples or [])

    def clean_name(self):
        # «Recordatorio de cita» → recordatorio_de_cita: lo que Meta admite.
        name = slugify(self.cleaned_data['name']).replace('-', '_')
        if not name:
            raise forms.ValidationError('Usa letras, números o guiones bajos.')
        return name

    def clean_header_text(self):
        header = ' '.join(self.cleaned_data['header_text'].split())
        if '{{' in header:
            raise forms.ValidationError('El encabezado no admite variables.')
        return header

    def clean_body(self):
        body = self.cleaned_data['body'].strip()
        # Meta no admite más de un salto de línea en blanco seguido.
        body = re.sub(r'\n{3,}', '\n\n', body.replace('\r\n', '\n'))

        variables = WhatsAppTemplate.variables_in(body)
        if variables != list(range(1, len(variables) + 1)):
            raise forms.ValidationError(
                'Las variables deben numerarse seguidas desde {{1}}: {{1}}, {{2}}, {{3}}…'
            )
        if re.match(r'\s*\{\{', body) or re.search(r'\}\}\s*$', body):
            raise forms.ValidationError(
                'El mensaje no puede empezar ni acabar con una variable: añade texto antes y después.'
            )
        return body

    def clean_footer(self):
        footer = ' '.join(self.cleaned_data['footer'].split())
        if '{{' in footer:
            raise forms.ValidationError('El pie no admite variables.')
        return footer

    def clean(self):
        cleaned = super().clean()
        name, language = cleaned.get('name'), cleaned.get('language')
        if self.fields['name'].disabled:
            name, language = self.instance.name, self.instance.language

        if name and language:
            duplicated = (
                WhatsAppTemplate.objects.filter(clinic=self.clinic, name=name, language=language)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if duplicated:
                self.add_error('name', 'Ya tienes una plantilla con ese nombre en este idioma.')

        body = cleaned.get('body')
        if body is not None and 'body' not in self.errors:
            count = len(WhatsAppTemplate.variables_in(body))
            examples = [value.strip() for value in self.examples[:count]]
            if len(examples) < count or not all(examples):
                self.add_error(None, 'Escribe un ejemplo para cada variable: Meta los pide para revisar la plantilla.')
            cleaned['body_examples'] = examples
        return cleaned

    def save(self, commit=True):
        template = super().save(commit=False)
        template.clinic = self.clinic
        template.body_examples = self.cleaned_data.get('body_examples', [])
        # Corregir una rechazada la devuelve a borrador: hay que reenviarla.
        if template.pk and template.status == WhatsAppTemplate.Status.REJECTED:
            template.status = WhatsAppTemplate.Status.DRAFT
            template.rejection_reason = ''
        if commit:
            template.save()
        return template
