from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from core.models import Clinic, User


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ('clinic_id', 'name', 'timezone', 'whatsapp_phone_number_id')
    search_fields = ('clinic_id', 'name')


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
