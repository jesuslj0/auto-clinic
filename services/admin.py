from django.contrib import admin

from services.models import Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'clinic', 'duration_minutes', 'price', 'is_active')
    search_fields = ('name',)
    list_filter = ('clinic', 'is_active')
