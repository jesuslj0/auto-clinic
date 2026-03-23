from django.contrib import admin

from billing.models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'plan_name', 'status', 'starts_at', 'ends_at', 'auto_renew')
    list_filter = ('status', 'auto_renew')
    search_fields = ('clinic__name', 'plan_name')
