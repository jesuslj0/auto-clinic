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
            'name', 'phone', 'email', 'address', 'city', 'province',
            'postal_code', 'timezone', 'description', 'api_type', 'api_url',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'api_url': forms.URLInput(),
        }
        labels = {
            'name': 'Nombre',
            'phone': 'Teléfono',
            'email': 'Correo electrónico',
            'address': 'Dirección',
            'city': 'Ciudad',
            'province': 'Provincia',
            'postal_code': 'Código postal',
            'description': 'Descripción',
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
