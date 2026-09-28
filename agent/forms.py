from django import forms

from agent.models import AgentProfile
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
    whatsapp_token = forms.CharField(
        label='Token de acceso permanente',
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Se almacena de forma segura y nunca se vuelve a mostrar. '
                  'Déjalo en blanco para conservar el token actual.',
    )

    class Meta:
        model = Clinic
        fields = ['whatsapp_phone_number_id', 'whatsapp_token']

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
