from django import forms
from django.core.files.base import ContentFile
from django.db import transaction
from django.forms import ModelForm, DateInput, Textarea

from patients.models import Patient, PatientGuardian
from patients.photos import delete_photo_object, prepare_patient_photo


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


class PatientEditForm(PatientForm):
    """Edición de la ficha: los datos del alta más la foto de perfil.

    Va aparte porque el alta pasa `cleaned_data` entero a `create_patient()`.
    """

    # La foto no es un campo del `Meta`: no se guarda tal cual llega, sino
    # validada, recortada y sin metadatos (`prepare_patient_photo`).
    photo = forms.FileField(
        label='Foto de perfil',
        required=False,
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
    )
    remove_photo = forms.BooleanField(label='Quitar la foto', required=False)

    def clean_photo(self):
        uploaded = self.cleaned_data.get('photo')
        if not uploaded:
            return None
        return prepare_patient_photo(uploaded)

    def save(self, commit=True):
        patient = super().save(commit=False)
        prepared = self.cleaned_data.get('photo')
        old_name = patient.photo.name if patient.photo else ''

        if prepared is not None:
            # `save=False`: el objeto se sube ya, la fila se guarda abajo junto
            # con el resto de campos (un solo `save()`, un solo `ChangeLog`).
            patient.photo.save('photo.jpg', ContentFile(prepared.content), save=False)
        elif self.cleaned_data.get('remove_photo'):
            patient.photo = None

        if commit:
            patient.save()
            new_name = patient.photo.name if patient.photo else ''
            if old_name and old_name != new_name:
                # Solo si el guardado se confirma: si se deshace, la ficha sigue
                # apuntando a la antigua y esa no puede haber desaparecido.
                transaction.on_commit(lambda: delete_photo_object(old_name))
        return patient


class GuardianForm(forms.Form):
    """Alta de un contacto responsable desde la ficha de un paciente."""

    first_name = forms.CharField(label='Nombre', max_length=150)
    last_name = forms.CharField(label='Apellidos', max_length=150, required=False)
    phone = forms.CharField(label='Teléfono', max_length=32, required=False)
    relationship = forms.ChoiceField(
        label='Relación con el paciente', choices=PatientGuardian.Relationship.choices
    )
