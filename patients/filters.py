import django_filters

from patients.models import Patient
from patients.services import normalize_phone_safe


class PhoneExactFilter(django_filters.CharFilter):
    def filter(self, qs, value):
        if not value:
            return qs
        # Con `patient_id` manda ese filtro: la ficha elegida puede ser de un
        # familiar con otro teléfono, y el teléfono sirve entonces para comprobar
        # que el número tiene derecho a ella, no para igualar.
        if self.parent.data.get('patient_id'):
            return qs
        normalized = normalize_phone_safe(value)
        if normalized is None:
            return qs.none()
        return super().filter(qs, normalized)


class PatientFilter(django_filters.FilterSet):
    phone = PhoneExactFilter(field_name="phone", lookup_expr="exact")
    # La ficha concreta que el agente quiere gestionar para el número de `phone`.
    # Solo devuelve algo si ese número puede reservar para ella (la suya o la de
    # un paciente vinculado a su contacto responsable); si no, lista vacía.
    patient_id = django_filters.NumberFilter(method='filter_patient_id')

    class Meta:
        model = Patient
        fields = ["clinic", "phone"]

    def filter_patient_id(self, queryset, name, value):
        from patients.services import can_book_for

        phone = self.data.get('phone')
        if not phone:
            return queryset.none()
        patient = queryset.filter(pk=value).select_related('clinic').first()
        if patient is None or not can_book_for(patient.clinic, phone, patient):
            return queryset.none()
        return queryset.filter(pk=patient.pk)
