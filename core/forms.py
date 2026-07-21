from django import forms
from django.contrib.auth.forms import AuthenticationForm

from core.models import Clinic


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autofocus': True, 'autocomplete': 'email'}),
    )
    password = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )


TIMEZONE_CHOICES = [
    ('Europe/Madrid', 'Europe/Madrid'),
    ('Europe/London', 'Europe/London'),
    ('Europe/Paris', 'Europe/Paris'),
    ('Europe/Berlin', 'Europe/Berlin'),
    ('Europe/Rome', 'Europe/Rome'),
    ('Europe/Lisbon', 'Europe/Lisbon'),
    ('Atlantic/Canary', 'Atlantic/Canary'),
]


class ClinicForm(forms.ModelForm):
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES,
        label='Zona horaria',
    )

    class Meta:
        model = Clinic
        fields = [
            'name', 'phone', 'email', 'website', 'address', 'city', 'province',
            'postal_code', 'timezone', 'description', 'logo', 'api_type', 'api_url',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'website': forms.URLInput(),
            'api_url': forms.URLInput(),
        }
        labels = {
            'name': 'Nombre',
            'phone': 'Teléfono',
            'email': 'Correo electrónico',
            'website': 'Sitio web',
            'address': 'Dirección',
            'city': 'Ciudad',
            'province': 'Provincia',
            'postal_code': 'Código postal',
            'description': 'Descripción',
            'logo': 'Logotipo',
            'api_type': 'Tipo de integración',
            'api_url': 'URL de la API',
        }

    def clean(self):
        cleaned_data = super().clean()
        api_type = cleaned_data.get('api_type')
        api_url = cleaned_data.get('api_url')
        if api_type and not api_url:
            self.add_error('api_url', 'La URL de la API es obligatoria cuando se configura un tipo de integración.')
        return cleaned_data


class WhatsAppIntegrationForm(forms.ModelForm):
    """Configuración del agente de WhatsApp de una clínica.

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
    whatsapp_verify_token = forms.CharField(
        label='Token de verificación del webhook',
        required=False,
        help_text='Cadena que tú eliges y que debes pegar también en el panel de Meta.',
    )

    class Meta:
        model = Clinic
        fields = ['whatsapp_phone_number_id', 'whatsapp_token', 'whatsapp_verify_token']

    def clean_whatsapp_token(self):
        token = self.cleaned_data.get('whatsapp_token', '').strip()
        # Envío en blanco → mantener el token ya almacenado.
        if not token:
            return self.instance.whatsapp_token
        return token
