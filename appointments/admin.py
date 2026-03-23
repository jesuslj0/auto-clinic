from django.contrib import admin

from appointments.models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'service', 'clinic', 'scheduled_at', 'status')
    search_fields = ('patient__first_name', 'patient__last_name', 'service__name')
    list_filter = ('clinic', 'status', 'scheduled_at')
    readonly_fields = ('confirmation_token',)
