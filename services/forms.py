from django import forms

from services.models import Service, ServiceCategory


class ServiceForm(forms.ModelForm):
    """
    La regla del rango (máximo > mínimo) no se repite aquí: `_post_clean` de
    ModelForm ya ejecuta `Service.clean()` y reparte sus errores por campo.

    La categoría se pide por nombre en vez de por desplegable de claves: así el
    mismo campo sirve para elegir una existente y para crear una nueva sin
    salir del formulario, que es lo que necesita una clínica montando su
    catálogo por primera vez.
    """

    category_name = forms.CharField(
        label='Categoría',
        max_length=100,
        required=False,
    )

    class Meta:
        model = Service
        fields = [
            'name',
            'description',
            'duration_type',
            'duration_minutes',
            'duration_max_minutes',
            'price_type',
            'price',
            'price_max',
            'is_active',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'name': 'Nombre',
            'description': 'Descripción',
            'duration_type': 'Tipo de duración',
            'duration_minutes': 'Duración (minutos)',
            'duration_max_minutes': 'Duración máxima (minutos)',
            'price_type': 'Tipo de precio',
            'price': 'Precio (€)',
            'price_max': 'Precio máximo (€)',
            'is_active': 'Activo',
        }

    def __init__(self, *args, clinic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic or getattr(self.instance, 'clinic', None)

        # Un envío que no trae el tipo describe un servicio de valor fijo, que
        # es como se comportaba el formulario antes de existir los rangos.
        for campo in ('duration_type', 'price_type'):
            self.fields[campo].required = False

        if self.instance.category_id:
            self.fields['category_name'].initial = self.instance.category.name

    @property
    def category_suggestions(self):
        """Categorías de la clínica, para el `datalist` de la plantilla."""
        if self.clinic is None:
            return ServiceCategory.objects.none()
        return ServiceCategory.objects.filter(clinic=self.clinic, is_active=True)

    def clean_duration_type(self):
        return self.cleaned_data.get('duration_type') or Service.ValueType.FIXED

    def clean_price_type(self):
        return self.cleaned_data.get('price_type') or Service.ValueType.FIXED

    def clean_category_name(self):
        return (self.cleaned_data.get('category_name') or '').strip()

    def save(self, commit=True):
        """Resuelve la categoría por nombre, creándola si la clínica no la tiene."""
        service = super().save(commit=False)
        clinic = self.clinic or service.clinic
        nombre = self.cleaned_data.get('category_name')

        if nombre and clinic is not None:
            # `iexact` para que "quiropodia" no cree una gemela de "Quiropodia".
            categoria = ServiceCategory.objects.filter(clinic=clinic, name__iexact=nombre).first()
            if categoria is None:
                categoria = ServiceCategory.objects.create(
                    clinic=clinic, name=nombre, color=ServiceCategory.next_color(clinic),
                )
            service.category = categoria
        else:
            service.category = None

        if commit:
            service.save()
        return service
