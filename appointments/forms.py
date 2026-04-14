from django import forms
from django.db.models import Q

from appointments.models import Professional
from core.models import User
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
