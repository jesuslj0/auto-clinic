from django import forms

from knowledge.models import ClinicKnowledgeBase

KB_TYPE_LABELS = [
    ('services', 'Servicios'),
    ('pricing', 'Tarifas'),
    ('schedule', 'Horarios'),
    ('faq', 'Preguntas frecuentes'),
    ('location', 'Ubicación'),
    ('policies', 'Políticas'),
    ('team', 'Equipo'),
]


class KnowledgeBaseForm(forms.ModelForm):
    kb_type = forms.ChoiceField(
        choices=KB_TYPE_LABELS,
        label='Categoría',
    )

    class Meta:
        model = ClinicKnowledgeBase
        fields = ['kb_type', 'title', 'content', 'active']
        widgets = {
            'content': forms.Textarea(attrs={'rows': 8}),
        }
        labels = {
            'title': 'Título',
            'content': 'Contenido',
            'active': 'Activo',
        }
