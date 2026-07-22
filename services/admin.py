from django.contrib import admin

from services.models import Service, ServiceCategory


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'clinic', 'color', 'is_active')
    search_fields = ('name',)
    list_filter = ('clinic', 'is_active')


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'clinic', 'category', 'duration_display', 'price_display', 'is_active')
    search_fields = ('name',)
    list_filter = ('clinic', 'is_active', 'category', 'price_type', 'duration_type')
