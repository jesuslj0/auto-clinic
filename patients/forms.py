from django.forms import ModelForm, DateInput, Textarea
from patients.models import Patient
from django.contrib import messages

class PatientForm(ModelForm):
    class Meta:
        model = Patient
        fields = ['first_name', 'last_name', 'email', 'phone', 'date_of_birth', 'notes']
        widgets = {
            'date_of_birth': DateInput(attrs={'type': 'date'}),
            'notes': Textarea(attrs={'rows': 3, 'placeholder': 'Añade notas relevantes del paciente...'}),
        }
        labels = {
            'first_name': 'Nombre',
            'last_name': 'Apellido',
            'email': 'Correo electrónico',
            'phone': 'Teléfono',
            'date_of_birth': 'Fecha de nacimiento',
            'notes': 'Notas',
        }