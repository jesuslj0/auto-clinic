from django.contrib import admin

from notifications.models import Reminder


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('appointment', 'reminder_type', 'scheduled_for', 'sent_at', 'success')
    list_filter = ('clinic', 'reminder_type', 'success')
