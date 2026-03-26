from django import forms

from services.models import Service


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ['name', 'description', 'duration_minutes', 'price', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'name': 'Nombre',
            'description': 'Descripción',
            'duration_minutes': 'Duración (minutos)',
            'price': 'Precio (€)',
            'is_active': 'Activo',
        }
