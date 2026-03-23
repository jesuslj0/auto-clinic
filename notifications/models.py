from django.db import models

from appointments.models import Appointment
from core.models import Clinic, TimeStampedModel


class Reminder(TimeStampedModel):
    class ReminderType(models.TextChoices):
        H24 = '24h', '24 Hours'
        H2 = '2h', '2 Hours'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='reminders')
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='reminders')
    reminder_type = models.CharField(max_length=10, choices=ReminderType.choices)
    scheduled_for = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)

    class Meta:
        ordering = ['scheduled_for']
        unique_together = ('appointment', 'reminder_type')

    def __str__(self):
        return f'{self.appointment} - {self.reminder_type}'
