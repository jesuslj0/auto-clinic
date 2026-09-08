from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from core.models import Clinic

#: Control de formulario del sistema de diseño. Sin color de borde ni de anillo:
#: los pone la variante (normal o error), que si no competirían entre sí.
_FIELD_BASE_CLASS = (
    'block w-full rounded-xl border bg-surface px-4 py-2.5 text-sm text-content '
    'placeholder-content-faint shadow-sm transition focus:border-transparent '
    'focus:outline-none focus:ring-2'
)
FIELD_CLASS = f'{_FIELD_BASE_CLASS} border-line-strong focus:ring-brand-500'
#: El estado de error se marca con el componente `.field-error` de
#: `_head_theme.html`, no con utilidades: este formulario viaja por htmx y las
#: utilidades que solo existan en la respuesta pueden no llegar a compilarse.
ERROR_FIELD_CLASS = f'{_FIELD_BASE_CLASS} field-error'

#: Mismas constantes y misma idea que en `clinical/forms.py`, repetidas a
#: propósito: `core` es la base sobre la que se apoya el resto de apps y no debe
#: importar de ninguna de ellas.


def error_id(field_name: str) -> str:
    """`id` del párrafo de error de un campo, para `aria-describedby`."""
    return f'error-{field_name}'


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


class AccountPasswordChangeForm(PasswordChangeForm):
    """Cambio de la propia contraseña, desde «Mi cuenta».

    Todo lo que importa lo hace ya `PasswordChangeForm` de Django y aquí no se
    reimplementa nada: exige la contraseña actual, pasa la nueva por
    `AUTH_PASSWORD_VALIDATORS`, comprueba que las dos copias coinciden y decora
    sus métodos con `@sensitive_variables` para que ninguna contraseña acabe en
    un informe de error. Esta subclase solo pone la piel.

    Dos avisos para quien la toque:

    - Los errores de los validadores se añaden a `new_password2`, no a
      `new_password1` (`SetPasswordForm.clean()`). Ahí es donde hay que buscarlos.
    - El `help_text` de `new_password1` es HTML ya marcado como seguro (un `<ul>`
      generado a partir de los validadores configurados), así que se pinta tal
      cual y la plantilla solo le da estilo de lista.

    Las etiquetas se sobrescriben aunque Django las traduzca: su traducción
    trata de usted («Su contraseña antigua es incorrecta») y toda la interfaz
    del panel tutea.
    """

    error_messages = {
        **PasswordChangeForm.error_messages,
        'password_incorrect': 'La contraseña actual no es correcta. Vuelve a introducirla.',
        'password_mismatch': 'Las dos contraseñas nuevas no coinciden.',
    }

    LABELS = {
        'old_password': 'Contraseña actual',
        'new_password1': 'Nueva contraseña',
        'new_password2': 'Repite la nueva contraseña',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, label in self.LABELS.items():
            field = self.fields[name]
            field.label = label
            # `autocomplete` y `autofocus` ya vienen puestos por Django.
            field.widget.attrs['class'] = FIELD_CLASS
        self.fields['new_password2'].help_text = (
            'Escríbela otra vez para asegurarnos de que no hay una errata.'
        )
        # El botón «Mostrar» de la plantilla destapa las DOS contraseñas nuevas
        # a la vez: teclear una y confirmarla a ciegas es la mitad de las
        # erratas. La actual se queda siempre oculta — no hay nada que revisar
        # en ella y es la que un mirón aprovecharía.
        #
        # El atributo va aquí porque el `type` lo escribe el widget, y solo
        # Alpine puede pisarlo; `show` lo declara el `x-data` del formulario.
        for name in ('new_password1', 'new_password2'):
            self.fields[name].widget.attrs[':type'] = "show ? 'text' : 'password'"

    def _post_clean(self):
        """Marca en rojo los campos que fallaron, y los anuncia a los lectores.

        Va aquí y no en `__init__` porque los errores no existen hasta que el
        formulario se valida: `_post_clean()` es el último paso de `full_clean()`,
        con `_errors` ya poblado. Mismo planteamiento que `ErrorHighlightMixin`
        en `clinical/forms.py`, sin sus casos especiales — aquí los tres campos
        son cajas de contraseña.
        """
        super()._post_clean()
        for name in self._errors:
            if name not in self.fields:
                continue
            widget = self.fields[name].widget
            widget.attrs['class'] = ERROR_FIELD_CLASS
            widget.attrs['aria-invalid'] = 'true'
            # El error se SUMA a la ayuda: Django apunta `aria-describedby` al
            # texto de ayuda al renderizar, pero se calla si el widget ya trae
            # el atributo. Sin esto, el campo con requisitos los perdería justo
            # cuando falla.
            described = []
            if self.fields[name].help_text:
                described.append(f'{self[name].auto_id}_helptext')
            described.append(error_id(name))
            widget.attrs['aria-describedby'] = ' '.join(described)

    def failed_on_old_password(self) -> bool:
        """¿El fallo fue al teclear la contraseña actual?

        Se mira el código del error y no la simple presencia de la clave: un
        campo obligatorio vacío también deja error en `old_password`, y eso no
        es alguien probando contraseñas. Lo usa el contador de intentos.
        """
        return any(
            error.code == 'password_incorrect'
            for error in self.errors.as_data().get('old_password', [])
        )
