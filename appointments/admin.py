from django.contrib import admin

from appointments.models import Appointment, Professional, ProfessionalSchedule


class ProfessionalScheduleInline(admin.TabularInline):
    model = ProfessionalSchedule
    extra = 0
    fields = ('day_of_week', 'start_time', 'end_time', 'is_active')


@admin.register(Professional)
class ProfessionalAdmin(admin.ModelAdmin):
    list_display = ('user', 'professional_type', 'clinic')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'clinic__name')
    list_filter = ('clinic', 'professional_type')
    filter_horizontal = ('services',)
    inlines = [ProfessionalScheduleInline]


@admin.register(ProfessionalSchedule)
class ProfessionalScheduleAdmin(admin.ModelAdmin):
    list_display = ('professional', 'day_of_week', 'start_time', 'end_time', 'is_active')
    list_filter = ('day_of_week', 'is_active', 'professional__clinic')
    search_fields = ('professional__user__first_name', 'professional__user__last_name')
    ordering = ('professional', 'day_of_week')


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'service', 'professional', 'clinic', 'scheduled_at', 'status')
    search_fields = ('patient__first_name', 'patient__last_name', 'service__name')
    list_filter = ('clinic', 'status', 'scheduled_at', 'professional')
    readonly_fields = ('confirmation_token',)
