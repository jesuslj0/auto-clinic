from django.db import models

from core.models import Clinic, TimeStampedModel


class Patient(TimeStampedModel):
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='patients')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32)
    date_of_birth = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        unique_together = ('clinic', 'email', 'phone')

    def __str__(self):
        return f'{self.first_name} {self.last_name}'
