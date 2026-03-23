"""
Root conftest.py: shared fixtures and factories for the entire test suite.
"""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from appointments.models import Appointment
from billing.models import Subscription
from core.models import Clinic, User
from notifications.models import Reminder
from patients.models import Patient
from services.models import Service


# ---------------------------------------------------------------------------
# Clinic fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clinic_a(db):
    return Clinic.objects.create(
        name="Clinic Alpha",
        slug="clinic-alpha",
        email="alpha@clinic.test",
        phone="111-0000",
        is_active=True,
    )


@pytest.fixture
def clinic_b(db):
    return Clinic.objects.create(
        name="Clinic Beta",
        slug="clinic-beta",
        email="beta@clinic.test",
        phone="222-0000",
        is_active=True,
    )


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db, clinic_a):
    return User.objects.create_user(
        email="admin@alpha.test",
        password="testpass123",
        clinic=clinic_a,
        role=User.Role.ADMIN,
        first_name="Admin",
        last_name="Alpha",
    )


@pytest.fixture
def staff_user(db, clinic_a):
    return User.objects.create_user(
        email="staff@alpha.test",
        password="testpass123",
        clinic=clinic_a,
        role=User.Role.STAFF,
        first_name="Staff",
        last_name="Alpha",
    )


@pytest.fixture
def admin_user_b(db, clinic_b):
    return User.objects.create_user(
        email="admin@beta.test",
        password="testpass123",
        clinic=clinic_b,
        role=User.Role.ADMIN,
    )


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        email="super@admin.test",
        password="testpass123",
    )


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_client(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def staff_client(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    return api_client


@pytest.fixture
def superuser_client(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    return api_client


@pytest.fixture
def client_b(api_client, admin_user_b):
    api_client.force_authenticate(user=admin_user_b)
    return api_client


# ---------------------------------------------------------------------------
# Domain object fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patient_a(db, clinic_a):
    return Patient.objects.create(
        clinic=clinic_a,
        first_name="John",
        last_name="Doe",
        email="john.doe@example.com",
        phone="555-0001",
    )


@pytest.fixture
def patient_b(db, clinic_b):
    return Patient.objects.create(
        clinic=clinic_b,
        first_name="Jane",
        last_name="Smith",
        email="jane.smith@example.com",
        phone="555-0002",
    )


@pytest.fixture
def service_a(db, clinic_a):
    return Service.objects.create(
        clinic=clinic_a,
        name="Consultation",
        duration_minutes=30,
        price="50.00",
        is_active=True,
    )


@pytest.fixture
def service_b(db, clinic_b):
    return Service.objects.create(
        clinic=clinic_b,
        name="Checkup",
        duration_minutes=60,
        price="80.00",
        is_active=True,
    )


@pytest.fixture
def appointment_a(db, clinic_a, patient_a, service_a, admin_user):
    now = timezone.now() + timedelta(hours=25)
    return Appointment.objects.create(
        clinic=clinic_a,
        patient=patient_a,
        service=service_a,
        assigned_to=admin_user,
        scheduled_at=now,
        end_at=now + timedelta(minutes=30),
        status=Appointment.Status.PENDING,
    )


@pytest.fixture
def appointment_b(db, clinic_b, patient_b, service_b):
    now = timezone.now() + timedelta(hours=25)
    return Appointment.objects.create(
        clinic=clinic_b,
        patient=patient_b,
        service=service_b,
        scheduled_at=now,
        end_at=now + timedelta(minutes=60),
        status=Appointment.Status.PENDING,
    )


@pytest.fixture
def subscription_a(db, clinic_a):
    return Subscription.objects.create(
        clinic=clinic_a,
        plan_name="Basic",
        status=Subscription.Status.ACTIVE,
        starts_at=timezone.now(),
    )


@pytest.fixture
def reminder_a(db, clinic_a, appointment_a):
    return Reminder.objects.create(
        clinic=clinic_a,
        appointment=appointment_a,
        reminder_type=Reminder.ReminderType.H24,
        scheduled_for=appointment_a.scheduled_at - timedelta(hours=24),
    )
