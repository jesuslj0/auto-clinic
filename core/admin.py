import uuid

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from core.models import Clinic, User


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ('clinic_id', 'name', 'timezone', 'whatsapp_phone_number_id')
    search_fields = ('clinic_id', 'name')
    raw_id_fields = ('test_patient',)
    # La clave que usa n8n para autenticarse. Se muestra aquí —el panel de la
    # clínica ya no la enseña— pero no se teclea a mano: se sustituye entera
    # con la acción de regenerar.
    readonly_fields = ('agent_api_key',)
    actions = ('regenerate_agent_api_key',)

    @admin.action(description='Regenerar la clave del agente (n8n)', permissions=['change'])
    def regenerate_agent_api_key(self, request, queryset):
        """Invalida la clave actual de las clínicas seleccionadas.

        Es la respuesta a una filtración: la anterior deja de autenticar en el
        mismo instante, así que hay que llevar la nueva a n8n justo después o
        el agente dejará de poder operar sobre los datos de esa clínica.
        """
        rotated = 0
        for clinic in queryset:
            clinic.agent_api_key = uuid.uuid4()
            clinic.save(update_fields=['agent_api_key'])
            # Deja rastro en el historial del admin de quién la rotó y cuándo.
            self.log_change(request, clinic, 'Clave del agente regenerada')
            rotated += 1

        self.message_user(
            request,
            f'{rotated} clínica(s) con clave nueva. Actualízala en n8n o el agente '
            f'dejará de responder.',
            messages.WARNING,
        )


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    list_display = ('email', 'clinic', 'role', 'is_staff', 'is_active')
    ordering = ('email',)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Clinic Access', {'fields': ('clinic', 'role', 'created_at', 'updated_at')}),
    )
    readonly_fields = ('created_at', 'updated_at')
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'clinic', 'role', 'is_staff', 'is_superuser'),
        }),
    )
