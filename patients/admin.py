from django.contrib import admin

from patients.models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'clinic', 'phone', 'email')
    search_fields = ('first_name', 'last_name', 'email', 'phone')
    list_filter = ('clinic',)
