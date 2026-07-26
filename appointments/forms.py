from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from appointments.models import Professional, ProfessionalSchedule, ProfessionalTimeOff
from core.models import User
from patients.models import Patient
from services.models import Service


class ProfessionalForm(forms.ModelForm):
    class Meta:
        model = Professional
        fields = ['user', 'professional_type', 'services']
        labels = {
            'user': 'Usuario',
            'professional_type': 'Tipo de profesional',
            'services': 'Servicios que ofrece',
        }
        widgets = {
            'services': forms.SelectMultiple(attrs={'size': 8}),
        }

    def __init__(self, *args, request_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_user = request_user

        # Alcance de plataforma (todas las clínicas) solo sin usuario de contexto
        # o para un superusuario SIN clínica. Un superusuario con clínica queda
        # acotado a ella igual que el staff.
        if request_user is None or (request_user.is_superuser and not request_user.clinic_id):
            self.fields['user'].queryset = User.objects.order_by('first_name', 'last_name', 'email')
            self.fields['services'].queryset = Service.objects.order_by('name')
            return

        if not request_user.clinic_id:
            self.fields['user'].queryset = User.objects.none()
            self.fields['services'].queryset = Service.objects.none()
            return

        user_queryset = User.objects.filter(clinic=request_user.clinic)
        if self.instance.pk:
            user_queryset = user_queryset.filter(Q(professional_profile__isnull=True) | Q(pk=self.instance.user_id))
        else:
            user_queryset = user_queryset.filter(professional_profile__isnull=True)

        self.fields['user'].queryset = user_queryset.order_by('first_name', 'last_name', 'email')
        self.fields['services'].queryset = Service.objects.filter(clinic=request_user.clinic).order_by('name')

    def clean_user(self):
        user = self.cleaned_data['user']
        if self.request_user and self.request_user.clinic_id:
            if user.clinic_id != self.request_user.clinic_id:
                raise forms.ValidationError('El usuario seleccionado no pertenece a tu clínica.')
        return user


class ProfessionalProfileForm(forms.ModelForm):
    """Perfil editable por el propio profesional autenticado.

    Combina datos personales del User (nombre/apellidos) con los del
    Professional (foto, tipo y servicios que ofrece).
    """

    first_name = forms.CharField(label='Nombre', max_length=150, required=False)
    last_name = forms.CharField(label='Apellidos', max_length=150, required=False)

    class Meta:
        model = Professional
        fields = ['photo', 'first_name', 'last_name', 'professional_type', 'services']
        labels = {
            'photo': 'Foto de perfil',
            'professional_type': 'Tipo de profesional',
            'services': 'Servicios que ofrece',
        }
        widgets = {
            'photo': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
            'services': forms.SelectMultiple(attrs={'size': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user_id:
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name

        if self.instance and self.instance.clinic_id:
            self.fields['services'].queryset = Service.objects.filter(
                clinic=self.instance.clinic
            ).order_by('name')
        else:
            self.fields['services'].queryset = Service.objects.none()

    def save(self, commit=True):
        professional = super().save(commit=commit)
        user = professional.user
        user.first_name = self.cleaned_data.get('first_name', '')
        user.last_name = self.cleaned_data.get('last_name', '')
        if commit:
            user.save(update_fields=['first_name', 'last_name'])
        return professional


class AppointmentForm(forms.Form):
    """
    Alta manual de citas desde el panel.

    No incluye `clinic` (se inyecta desde request.user.clinic) ni `status`
    (la cita nace siempre 'pending' a través del service). La fecha y la hora
    se introducen en el timezone de la clínica y se convierten a UTC en clean().
    """

    patient = forms.ModelChoiceField(
        queryset=Patient.objects.none(),
        label='Paciente',
        empty_label='— Selecciona un paciente —',
    )
    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        label='Servicio',
        empty_label='— Selecciona un servicio —',
    )
    professional = forms.ModelChoiceField(
        queryset=Professional.objects.none(),
        label='Profesional',
        empty_label='— Selecciona un profesional —',
    )
    date = forms.DateField(
        label='Fecha',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    time = forms.TimeField(
        label='Hora',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    notes = forms.CharField(
        label='Notas',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def __init__(self, *args, clinic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic

        if clinic is None:
            return

        self.fields['patient'].queryset = Patient.objects.filter(clinic=clinic).order_by(
            'last_name', 'first_name'
        )
        self.fields['service'].queryset = Service.objects.filter(
            clinic=clinic, is_active=True
        ).order_by('name')
        self.fields['professional'].queryset = Professional.objects.filter(
            clinic=clinic, is_active=True
        ).select_related('user').order_by('user__first_name', 'user__last_name')

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get('date')
        time_value = cleaned.get('time')

        if date and time_value:
            tz = ZoneInfo(self.clinic.timezone) if self.clinic else timezone.get_current_timezone()
            local_dt = datetime.combine(date, time_value).replace(tzinfo=tz)
            scheduled_at = local_dt.astimezone(ZoneInfo('UTC'))

            if scheduled_at < timezone.now():
                raise forms.ValidationError(
                    {'date': 'La cita no puede programarse en el pasado.'}
                )

            cleaned['scheduled_at'] = scheduled_at

        return cleaned


# ---------------------------------------------------------------------------
# Horario recurrente y ausencias del profesional (inline formsets del edit)
# ---------------------------------------------------------------------------
#
# Ambos cuelgan del edit de un profesional YA existente, así que su clínica —y
# por tanto su timezone— se conoce sin ambigüedad. Ver `ProfessionalUpdateView`.

# Clase de input compartida con el resto del panel, para que las filas de los
# formsets no desentonen con el form del profesional. Vive aquí (y no repetida en
# el template) para poder usar `{{ campo }}` directo y que el widget ya venga
# estilizado.
_FIELD_CLASS = (
    'block w-full rounded-xl border border-line-strong bg-surface px-3 py-2 text-sm '
    'text-content shadow-sm focus:border-transparent focus:outline-none '
    'focus:ring-2 focus:ring-brand-500 transition'
)


class ProfessionalScheduleForm(forms.ModelForm):
    """Un tramo del horario semanal. `start_time`/`end_time` son hora LOCAL.

    No hay conversión de zona horaria: un horario recurrente no tiene UTC (ver el
    docstring de `ProfessionalSchedule`). La materialización a instantes absolutos
    ocurre en `services.py`, no aquí.
    """

    class Meta:
        model = ProfessionalSchedule
        fields = ['day_of_week', 'start_time', 'end_time', 'is_active']
        widgets = {
            'day_of_week': forms.Select(attrs={'class': _FIELD_CLASS}),
            'start_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': _FIELD_CLASS}),
            'end_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': _FIELD_CLASS}),
            'is_active': forms.CheckboxInput(
                attrs={'class': 'h-4 w-4 rounded border-line-strong text-brand-600 focus:ring-brand-500'}
            ),
        }


class BaseScheduleFormSet(forms.BaseInlineFormSet):
    """Detecta solapamientos ENTRE las filas del propio POST.

    `ProfessionalSchedule.clean()` valida contra lo ya guardado en BD, pero durante
    `is_valid()` ninguna fila nueva está aún en BD, así que dos tramos nuevos que
    se pisan pasarían su validación individual. Este `clean()` cierra ese hueco.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        tramos = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            dia = form.cleaned_data.get('day_of_week')
            inicio = form.cleaned_data.get('start_time')
            fin = form.cleaned_data.get('end_time')
            if dia is None or inicio is None or fin is None:
                continue
            for otro_dia, otro_inicio, otro_fin in tramos:
                if dia == otro_dia and inicio < otro_fin and fin > otro_inicio:
                    raise forms.ValidationError(
                        'Hay dos tramos que se solapan el mismo día. Revísalos.'
                    )
            tramos.append((dia, inicio, fin))


class ProfessionalTimeOffForm(forms.ModelForm):
    """Una ausencia puntual. `starts_at`/`ends_at` son instantes en UTC.

    El staff los teclea en hora LOCAL de la clínica; aquí se convierte a UTC al
    guardar y de vuelta a local al mostrar. Misma responsabilidad que
    `AppointmentForm`: la BD guarda UTC, la UI habla en hora de la clínica.
    """

    class Meta:
        model = ProfessionalTimeOff
        fields = ['starts_at', 'ends_at', 'reason', 'note']
        widgets = {
            'starts_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': _FIELD_CLASS}),
            'ends_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': _FIELD_CLASS}),
            'reason': forms.Select(attrs={'class': _FIELD_CLASS}),
            'note': forms.TextInput(attrs={'placeholder': 'Opcional', 'class': _FIELD_CLASS}),
        }

    def __init__(self, *args, clinic_tz=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic_tz = clinic_tz or ZoneInfo(timezone.get_current_timezone_name())
        # El input datetime-local no lleva zona: acepta 'YYYY-MM-DDTHH:MM'.
        for campo in ('starts_at', 'ends_at'):
            self.fields[campo].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S']

        # Prellenar en hora local: la instancia guarda UTC, el widget muestra local.
        if self.instance and self.instance.pk:
            for campo in ('starts_at', 'ends_at'):
                valor = getattr(self.instance, campo)
                if valor:
                    self.initial[campo] = valor.astimezone(self.clinic_tz).strftime('%Y-%m-%dT%H:%M')

    def _to_utc(self, valor):
        """Interpreta un datetime naive del input como hora local y lo pasa a UTC."""
        if valor and timezone.is_naive(valor):
            return valor.replace(tzinfo=self.clinic_tz).astimezone(ZoneInfo('UTC'))
        return valor

    def clean_starts_at(self):
        return self._to_utc(self.cleaned_data.get('starts_at'))

    def clean_ends_at(self):
        return self._to_utc(self.cleaned_data.get('ends_at'))


ScheduleFormSet = inlineformset_factory(
    Professional, ProfessionalSchedule,
    form=ProfessionalScheduleForm, formset=BaseScheduleFormSet,
    extra=0, can_delete=True,
)

TimeOffFormSet = inlineformset_factory(
    Professional, ProfessionalTimeOff,
    form=ProfessionalTimeOffForm,
    extra=0, can_delete=True,
)
