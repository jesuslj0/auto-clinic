from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Clinic(models.Model):
    clinic_id = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=50, default="Europe/Madrid")
    whatsapp_phone_number_id = models.CharField(max_length=100, blank=True)

    # Integración de calendario
    api_type = models.CharField(
        max_length=20,
        choices=[
            ("calendly", "Calendly"),
            ("google_calendar", "Google Calendar"),
            ("custom", "Custom"),
        ],
        blank=True,
    )
    api_url = models.CharField(max_length=500, blank=True)
    api_key = models.CharField(max_length=500, blank=True)

    # Calendly
    calendly_link = models.CharField(max_length=500, blank=True)
    calendly_token = models.CharField(max_length=500, blank=True)
    calendly_event_type_uuid = models.UUIDField(null=True, blank=True)

    # Google Calendar
    google_calendar_id = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "clinics"

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, username=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser, TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        STAFF = 'staff', 'Staff'

    username = models.CharField(max_length=150, unique=True, blank=True)
    email = models.EmailField(unique=True)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='users', null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ['email']

    def save(self, *args, **kwargs):
        self.username = self.email
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
