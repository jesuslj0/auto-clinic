from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.db.models import Q
from django.utils import timezone

from appointments.models import Professional
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

        if not request_user or request_user.is_superuser:
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
        if self.request_user and not self.request_user.is_superuser and self.request_user.clinic_id:
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
