"""Formularios de facturación.

Una factura de paciente **no se teclea**: se compone. Sus importes no los
escribe nadie —salen congelados de los procedimientos ya registrados— y su
número lo pone la serie de la clínica al emitir. Por eso este formulario solo
pregunta dos cosas: a quién se le factura y qué procedimientos entran.

Lo que NO es un campo, y no debe llegar a serlo:

- `total` — se calcula de las líneas (`refresh_total()`), y una vez emitida está
  congelado. Un campo editable dejaría cobrar un importe que no cuadra con nada.
- `number` e `issued_at` — los pone `issue()`. Teclear el número a mano rompería
  la serie correlativa, que es justo lo que no puede tener saltos.
- `frozen_*` — son copias que hace el modelo, no datos de entrada.
"""
from django import forms

from billing.filters import pending_procedures_for
from patients.models import Patient


def pending_for_patient(user, patient):
    """Procedimientos de `patient` que aún puede cobrar `user`.

    Se apoya en `pending_procedures_for()` —el mismo queryset que alimenta el KPI
    «pendiente de facturar»— y no en una consulta propia: si el panel dice que
    hay 450 € por cobrar, el formulario tiene que ofrecer exactamente esos, no
    una lista parecida. De ahí sale también el aislamiento por clínica.
    """
    from clinical.models import PerformedProcedure

    if patient is None:
        return PerformedProcedure.objects.none()
    return (
        pending_procedures_for(user)
        .filter(visit__episode__history__patient=patient)
        .order_by('performed_at', 'id')
    )


class PatientInvoiceForm(forms.Form):
    """Alta de una factura: paciente, procedimientos y si se emite ya.

    No es un `ModelForm` a propósito. Un `ModelForm` sobre `PatientInvoice`
    expondría campos que el modelo se reserva (`total`, `number`, `lines`) y
    dejaría la puerta abierta a que un `fields = '__all__'` de mañana los
    hiciera editables. Aquí solo existe lo que una persona decide.

    El queryset de `procedures` se acota al paciente **en el `__init__`**, con
    los datos que traiga el POST. Es la parte delicada: si se dejara abierto, un
    formulario manipulado podría enganchar a esta factura procedimientos de otro
    paciente o de otra clínica. Con el queryset acotado, un id que no esté en él
    es sencillamente inválido y Django lo rechaza solo.
    """

    patient = forms.ModelChoiceField(
        queryset=Patient.objects.none(),
        label='Paciente',
        empty_label='Selecciona un paciente',
        error_messages={'required': 'Elige a quién se le factura.'},
    )
    procedures = forms.ModelMultipleChoiceField(
        queryset=None,
        label='Procedimientos a facturar',
        widget=forms.CheckboxSelectMultiple,
        required=False,
        error_messages={
            'invalid_choice': 'Alguno de los procedimientos ya no está '
                              'disponible para facturar. Revisa la selección.',
        },
    )
    issue_now = forms.BooleanField(
        label='Emitir la factura al guardar',
        required=False,
        help_text='Al emitirla toma número, congela su importe y deja de poder '
                  'editarse. Si lo dejas sin marcar, se guarda como borrador.',
    )

    def __init__(self, *args, user, patient=None, **kwargs):
        """`user` acota la clínica; `patient`, si viene, fija el destinatario.

        `patient` llega cuando se factura desde la ficha de alguien concreto: en
        ese caso el campo se deja deshabilitado y su valor se impone en
        `clean_patient()`, para que un POST no pueda cambiarlo por el camino.
        """
        super().__init__(*args, **kwargs)
        self.user = user
        self.locked_patient = patient

        patients = Patient.objects.all()
        if user.clinic_id:
            patients = patients.filter(clinic=user.clinic)
        elif not user.is_superuser:
            patients = patients.none()
        self.fields['patient'].queryset = patients.order_by('last_name', 'first_name')

        if patient is not None:
            self.fields['patient'].initial = patient
            self.fields['patient'].disabled = True

        # El paciente que manda para acotar los procedimientos: el fijado por la
        # URL, o el que venga en el POST (una recarga del formulario con
        # errores tiene que seguir enseñando la lista correcta).
        self.selected_patient = patient or self._patient_from_data()
        self.fields['procedures'].queryset = pending_for_patient(
            user, self.selected_patient,
        )

    def _patient_from_data(self):
        """El paciente elegido en el POST, validado contra el queryset del campo."""
        if not self.is_bound:
            return None
        raw = self.data.get(self.add_prefix('patient'))
        if not raw:
            return None
        return self.fields['patient'].queryset.filter(pk=raw).first()

    def clean_patient(self):
        # Un campo `disabled` no se lee del POST, pero dejarlo explícito evita
        # que un cambio futuro en el widget abra el hueco sin que nadie lo note.
        if self.locked_patient is not None:
            return self.locked_patient
        return self.cleaned_data['patient']

    def clean(self):
        cleaned = super().clean()
        procedures = cleaned.get('procedures')

        # Emitir exige contenido: `issue()` lo rechazaría con `EmptyInvoice`, y
        # es mejor decirlo aquí que dejar reventar el guardado a medias.
        if cleaned.get('issue_now') and not procedures:
            self.add_error(
                'procedures',
                'Marca al menos un procedimiento: una factura vacía no se '
                'puede emitir. Si aún no sabes qué entra, guárdala como borrador.',
            )
        return cleaned


class InvoiceVoidForm(forms.Form):
    """Anulación de una factura emitida.

    El motivo es obligatorio aunque el modelo lo admita vacío: anular gasta un
    número de la serie para siempre y deja un documento sin efecto en el
    historial del paciente. Quien lo haga tiene que decir por qué, porque es lo
    único que explicará esa factura anulada dentro de tres años.
    """

    reason = forms.CharField(
        label='Motivo de la anulación',
        widget=forms.Textarea(attrs={'rows': 3}),
        max_length=500,
        error_messages={'required': 'Indica por qué se anula la factura.'},
    )
