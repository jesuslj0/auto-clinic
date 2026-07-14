from django.contrib import admin

from appointments.models import Appointment, Professional, ProfessionalSchedule, ProfessionalTimeOff
from appointments.services import (
    select_professional_for_appointment,
    validate_professional_for_appointment,
)


class ProfessionalScheduleInline(admin.TabularInline):
    model = ProfessionalSchedule
    extra = 0
    fields = ('day_of_week', 'start_time', 'end_time', 'is_active')


class ProfessionalTimeOffInline(admin.TabularInline):
    model = ProfessionalTimeOff
    extra = 0
    fields = ('starts_at', 'ends_at', 'reason', 'note')


@admin.register(Professional)
class ProfessionalAdmin(admin.ModelAdmin):
    list_display = ('user', 'professional_type', 'clinic', 'is_active', 'accepts_online_booking')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'clinic__name')
    list_filter = ('clinic', 'professional_type', 'is_active', 'accepts_online_booking')
    filter_horizontal = ('services',)
    inlines = [ProfessionalScheduleInline, ProfessionalTimeOffInline]


@admin.register(ProfessionalTimeOff)
class ProfessionalTimeOffAdmin(admin.ModelAdmin):
    list_display = ('professional', 'starts_at', 'ends_at', 'reason', 'note')
    list_filter = ('reason', 'professional__clinic')
    search_fields = ('professional__user__first_name', 'professional__user__last_name', 'note')
    ordering = ('-starts_at',)


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

    def save_model(self, request, obj, form, change):
        """Ninguna cita se guarda sin profesional ni con uno que no pueda atenderla.

        El admin es vía staff: se relaja `accepts_online_booking`, pero el resto
        de criterios (horario, ausencias, solapamiento) se validan igual que en
        la API. Si `professional` se deja vacío, se auto-asigna.
        """
        if obj.professional_id is None:
            obj.professional = select_professional_for_appointment(
                clinic=obj.clinic,
                service=obj.service,
                scheduled_at=obj.scheduled_at,
                end_at=obj.get_end_datetime(),
                require_online_booking=False,
            )
        else:
            validate_professional_for_appointment(
                professional=obj.professional,
                clinic=obj.clinic,
                service=obj.service,
                scheduled_at=obj.scheduled_at,
                end_at=obj.get_end_datetime(),
                require_online_booking=False,
                exclude_pk=obj.pk if change else None,
            )
        super().save_model(request, obj, form, change)
