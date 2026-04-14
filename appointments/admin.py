from django.contrib import admin

from appointments.models import Appointment, Professional


@admin.register(Professional)
class ProfessionalAdmin(admin.ModelAdmin):
    list_display = ('user', 'clinic')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'clinic__name')
    list_filter = ('clinic',)
    filter_horizontal = ('services',)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'service', 'professional', 'clinic', 'scheduled_at', 'status')
    search_fields = ('patient__first_name', 'patient__last_name', 'service__name')
    list_filter = ('clinic', 'status', 'scheduled_at', 'professional')
    readonly_fields = ('confirmation_token',)
