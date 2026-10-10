from django.contrib import admin

from patients.models import Guardian, Patient, PatientGuardian


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'clinic', 'phone', 'email')
    search_fields = ('first_name', 'last_name', 'email', 'phone')
    list_filter = ('clinic',)


class PatientGuardianInline(admin.TabularInline):
    model = PatientGuardian
    extra = 0
    raw_id_fields = ('patient',)
    fields = ('patient', 'relationship')


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    """Contactos responsables: solo logística de citas, nunca datos clínicos."""

    list_display = ('first_name', 'last_name', 'clinic', 'phone', 'email', 'patient')
    search_fields = ('first_name', 'last_name', 'email', 'phone')
    list_filter = ('clinic',)
    raw_id_fields = ('patient',)
    inlines = [PatientGuardianInline]
