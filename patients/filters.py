import django_filters

from patients.models import Patient
from patients.services import normalize_phone_safe


class PhoneExactFilter(django_filters.CharFilter):
    def filter(self, qs, value):
        if not value:
            return qs
        normalized = normalize_phone_safe(value)
        if normalized is None:
            return qs.none()
        return super().filter(qs, normalized)


class PatientFilter(django_filters.FilterSet):
    phone = PhoneExactFilter(field_name="phone", lookup_expr="exact")

    class Meta:
        model = Patient
        fields = ["clinic", "phone"]
