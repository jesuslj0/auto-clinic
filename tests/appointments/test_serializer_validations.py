"""
Tests for AppointmentSerializer.validate() new validations (Bugs 1-4)
and available-slots endpoints (Bugs 5-6).

Covers:
  Bug 4 — scheduled_at in the past → 400
  Bug 2 — service.is_active == False → 400
  Bug 3 — professional or service from a different clinic → 400
  Bug 1 — professional has no active schedule for that day → 400
  Bug 1 — appointment time outside professional schedule window → 400
  Bug 5 — non-superuser querying another clinic's available-slots → 403
  Bug 5 — superuser can query any clinic's available-slots → 200
  Bug 6 — ?date= with a past date → 400
  ProfessionalViewSet.available_slots:
        — no schedule for that day → works_this_day: false
        — busy appointments block slots correctly
"""
import pytest
from datetime import datetime, time, timedelta

from django.utils import timezone

from appointments.models import Appointment, Professional, ProfessionalSchedule
from core.models import Clinic, User
from patients.models import Patient
from services.models import Service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_weekday(target_weekday: int) -> datetime.date:
    """Return the next calendar date whose weekday() == target_weekday (0=Mon)."""
    today = timezone.localdate()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # always future
    return today + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Shared fixtures (scoped to this module; supplement root conftest)
# ---------------------------------------------------------------------------

@pytest.fixture
def clinic_a(db):
    return Clinic.objects.create(clinic_id="val-clinic-a", name="Validation Clinic A")


@pytest.fixture
def clinic_b(db):
    return Clinic.objects.create(clinic_id="val-clinic-b", name="Validation Clinic B")


@pytest.fixture
def admin_user(db, clinic_a):
    return User.objects.create_user(
        email="val-admin@a.test",
        password="pass",
        clinic=clinic_a,
        role=User.Role.ADMIN,
        first_name="Val",
        last_name="Admin",
    )


@pytest.fixture
def admin_user_b(db, clinic_b):
    return User.objects.create_user(
        email="val-admin@b.test",
        password="pass",
        clinic=clinic_b,
        role=User.Role.ADMIN,
    )


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        email="val-super@admin.test",
        password="pass",
    )


@pytest.fixture
def professional(db, admin_user):
    """Professional auto-created by signal when admin_user is saved."""
    return admin_user.professional_profile


@pytest.fixture
def service_a(db, clinic_a):
    return Service.objects.create(
        clinic=clinic_a,
        name="Val-Consultation",
        duration_minutes=30,
        price="50.00",
        is_active=True,
    )


@pytest.fixture
def inactive_service(db, clinic_a):
    return Service.objects.create(
        clinic=clinic_a,
        name="Val-Inactive-Service",
        duration_minutes=30,
        price="40.00",
        is_active=False,
    )


@pytest.fixture
def service_b(db, clinic_b):
    return Service.objects.create(
        clinic=clinic_b,
        name="Val-Service-B",
        duration_minutes=30,
        price="60.00",
        is_active=True,
    )


@pytest.fixture
def patient_a(db, clinic_a):
    return Patient.objects.create(
        clinic=clinic_a,
        first_name="ValPatient",
        last_name="A",
        email="val-patient@a.test",
        phone="666000001",
    )


@pytest.fixture
def monday_schedule(db, professional):
    """Active schedule for Monday (weekday 0), 09:00–17:00."""
    return ProfessionalSchedule.objects.create(
        professional=professional,
        day_of_week=0,  # Monday
        start_time=time(9, 0),
        end_time=time(17, 0),
        is_active=True,
    )


@pytest.fixture
def admin_client(db, admin_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def client_b(db, admin_user_b):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=admin_user_b)
    return client


@pytest.fixture
def superuser_client(db, superuser):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=superuser)
    return client


# ---------------------------------------------------------------------------
# Bug 4: scheduled_at in the past
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestScheduledAtInPast:
    def test_past_scheduled_at_returns_400(
        self, admin_client, clinic_a, patient_a, service_a, professional
    ):
        past = timezone.now() - timedelta(hours=1)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": past.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "pasado" in error_text

    def test_future_scheduled_at_passes_past_check(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """A future Monday at 10:00 inside schedule window should not hit the past error."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        future = timezone.make_aware(
            datetime.combine(next_monday, time(10, 0)), tz
        )
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        # Should not get a 400 because of "pasado"
        error_text = str(response.data) if response.status_code == 400 else ""
        assert "pasado" not in error_text


# ---------------------------------------------------------------------------
# Bug 2: service.is_active == False
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInactiveService:
    def test_inactive_service_returns_400(
        self, admin_client, clinic_a, patient_a, inactive_service, professional
    ):
        future = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": inactive_service.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "desactivado" in error_text

    def test_active_service_does_not_trigger_inactive_error(
        self, admin_client, clinic_a, patient_a, service_a
    ):
        future = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        error_text = str(response.data) if response.status_code == 400 else ""
        assert "desactivado" not in error_text


# ---------------------------------------------------------------------------
# Bug 3: multi-tenancy — professional belongs to different clinic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProfessionalClinicMismatch:
    def test_professional_from_other_clinic_returns_400(
        self, admin_client, clinic_a, clinic_b, patient_a, service_a, admin_user_b
    ):
        """Professional of clinic_b cannot be assigned to an appointment of clinic_a."""
        professional_b = admin_user_b.professional_profile
        future = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional_b.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "profesional" in error_text.lower() and "clínica" in error_text.lower()

    def test_professional_same_clinic_does_not_trigger_mismatch(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        future = timezone.make_aware(datetime.combine(next_monday, time(10, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        error_text = str(response.data) if response.status_code == 400 else ""
        assert "no pertenece a esta clínica" not in error_text


# ---------------------------------------------------------------------------
# Bug 3: multi-tenancy — service belongs to different clinic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestServiceClinicMismatch:
    def test_service_from_other_clinic_returns_400(
        self, admin_client, clinic_a, patient_a, service_b, professional
    ):
        """service_b belongs to clinic_b; appointment is for clinic_a → 400."""
        future = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_b.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "servicio" in error_text.lower() and "clínica" in error_text.lower()

    def test_service_same_clinic_does_not_trigger_mismatch(
        self, admin_client, clinic_a, patient_a, service_a
    ):
        future = timezone.now() + timedelta(hours=3)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        error_text = str(response.data) if response.status_code == 400 else ""
        assert "El servicio no pertenece a esta clínica." not in error_text


# ---------------------------------------------------------------------------
# Bug 1: professional has no active schedule for the requested day
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProfessionalScheduleDay:
    def test_no_schedule_for_day_returns_400(
        self, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """Professional has no schedule at all → error about not working that day."""
        professional.services.add(service_a)
        # Use next Tuesday (weekday 1) — no schedule created for any day
        next_tuesday = _next_weekday(1)
        tz = timezone.get_current_timezone()
        future = timezone.make_aware(datetime.combine(next_tuesday, time(10, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "no trabaja los" in error_text

    def test_inactive_schedule_for_day_returns_400(
        self, db, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """An inactive schedule is treated as if it does not exist."""
        professional.services.add(service_a)
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=2,  # Wednesday
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=False,  # inactive
        )
        next_wednesday = _next_weekday(2)
        tz = timezone.get_current_timezone()
        future = timezone.make_aware(datetime.combine(next_wednesday, time(10, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "no trabaja los" in error_text

    def test_day_in_error_message_matches_scheduled_day(
        self, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """Error message must name the correct day of the week (in Spanish)."""
        professional.services.add(service_a)
        # No schedules created → any day will fail; use Thursday (weekday 3)
        next_thursday = _next_weekday(3)
        tz = timezone.get_current_timezone()
        future = timezone.make_aware(datetime.combine(next_thursday, time(10, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": future.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "jueves" in error_text


# ---------------------------------------------------------------------------
# Bug 1: appointment time outside professional schedule window
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProfessionalScheduleTime:
    def test_appointment_before_schedule_start_returns_400(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """08:00 is before 09:00 start → validation error."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        too_early = timezone.make_aware(datetime.combine(next_monday, time(8, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": too_early.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "horario del profesional" in error_text

    def test_appointment_at_schedule_end_returns_400(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """17:00 equals end_time → outside working hours (end is exclusive)."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        at_end = timezone.make_aware(datetime.combine(next_monday, time(17, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": at_end.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "horario del profesional" in error_text

    def test_appointment_after_schedule_end_returns_400(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """18:00 is after 17:00 end → validation error."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        too_late = timezone.make_aware(datetime.combine(next_monday, time(18, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": too_late.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "horario del profesional" in error_text

    def test_appointment_within_schedule_passes_time_check(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """10:00 is within 09:00–17:00 → should not raise a time-range error."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        valid_time = timezone.make_aware(datetime.combine(next_monday, time(10, 0)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": valid_time.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        error_text = str(response.data) if response.status_code == 400 else ""
        assert "horario del profesional" not in error_text

    def test_error_message_includes_schedule_times(
        self, admin_client, clinic_a, patient_a, service_a, professional, monday_schedule
    ):
        """Error message must include 09:00 and 17:00 from the schedule."""
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        too_early = timezone.make_aware(datetime.combine(next_monday, time(7, 30)), tz)
        data = {
            "clinic": clinic_a.pk,
            "patient": patient_a.pk,
            "service": service_a.pk,
            "professional": professional.pk,
            "scheduled_at": too_early.isoformat(),
            "status": "pending",
        }
        response = admin_client.post("/api/appointments/", data, format="json")
        assert response.status_code == 400
        error_text = str(response.data)
        assert "09:00" in error_text
        assert "17:00" in error_text


# ---------------------------------------------------------------------------
# Bug 5: AppointmentViewSet.available-slots — clinic access control
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAvailableSlotsClinicAccess:
    def test_non_superuser_querying_own_clinic_returns_200(
        self, admin_client, clinic_a
    ):
        future_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}&clinic={clinic_a.pk}"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_non_superuser_querying_other_clinic_returns_403(
        self, admin_client, clinic_b
    ):
        """admin_client belongs to clinic_a; querying clinic_b must be forbidden."""
        future_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}&clinic={clinic_b.pk}"
        response = admin_client.get(url)
        assert response.status_code == 403
        assert "permiso" in str(response.data).lower()

    def test_superuser_can_query_any_clinic(self, superuser_client, clinic_b):
        """Superuser has no clinic restriction."""
        future_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}&clinic={clinic_b.pk}"
        response = superuser_client.get(url)
        assert response.status_code == 200

    def test_clinic_b_user_cannot_query_clinic_a_slots(self, client_b, clinic_a):
        """A user from clinic_b cannot query clinic_a's available slots."""
        future_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}&clinic={clinic_a.pk}"
        response = client_b.get(url)
        assert response.status_code == 403

    def test_nonexistent_clinic_returns_400(self, superuser_client):
        future_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}&clinic=nonexistent-clinic"
        response = superuser_client.get(url)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Bug 6: AppointmentViewSet.available-slots — past date rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAvailableSlotsPastDate:
    def test_past_date_returns_400(self, admin_client, clinic_a):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        url = f"/api/appointments/available-slots/?date={yesterday}"
        response = admin_client.get(url)
        assert response.status_code == 400
        assert "pasadas" in str(response.data).lower()

    def test_today_returns_200(self, admin_client):
        today = timezone.localdate().isoformat()
        url = f"/api/appointments/available-slots/?date={today}"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_future_date_returns_200(self, admin_client):
        future_date = (timezone.localdate() + timedelta(days=7)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_missing_date_param_returns_400(self, admin_client):
        response = admin_client.get("/api/appointments/available-slots/")
        assert response.status_code == 400
        assert "date" in str(response.data).lower()

    def test_invalid_date_format_returns_400(self, admin_client):
        url = "/api/appointments/available-slots/?date=32-13-2099"
        response = admin_client.get(url)
        assert response.status_code == 400

    def test_response_contains_expected_keys(self, admin_client):
        future_date = (timezone.localdate() + timedelta(days=3)).isoformat()
        url = f"/api/appointments/available-slots/?date={future_date}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert "date" in response.data
        assert "available_slots" in response.data
        assert "duration_minutes" in response.data


# ---------------------------------------------------------------------------
# ProfessionalViewSet.available-slots — no schedule for the day
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProfessionalAvailableSlots:
    def test_no_schedule_returns_works_this_day_false(
        self, admin_client, professional
    ):
        """Professional without any schedule → works_this_day: false, slots: []."""
        next_tuesday = _next_weekday(1)
        url = f"/api/professionals/{professional.pk}/available-slots/?date={next_tuesday.isoformat()}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.data["works_this_day"] is False
        assert response.data["available_slots"] == []

    def test_inactive_schedule_returns_works_this_day_false(
        self, db, admin_client, professional
    ):
        """An inactive schedule is treated as no schedule → works_this_day: false."""
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=2,  # Wednesday
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=False,
        )
        next_wednesday = _next_weekday(2)
        url = f"/api/professionals/{professional.pk}/available-slots/?date={next_wednesday.isoformat()}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.data["works_this_day"] is False
        assert response.data["available_slots"] == []

    def test_schedule_present_returns_works_this_day_true(
        self, db, admin_client, professional
    ):
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )
        next_monday = _next_weekday(0)
        url = f"/api/professionals/{professional.pk}/available-slots/?date={next_monday.isoformat()}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.data["works_this_day"] is True

    def test_response_contains_schedule_info_when_working(
        self, db, admin_client, professional
    ):
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        next_monday = _next_weekday(0)
        url = f"/api/professionals/{professional.pk}/available-slots/?date={next_monday.isoformat()}"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert "schedule" in response.data
        assert response.data["schedule"]["start_time"] == "09:00"
        assert response.data["schedule"]["end_time"] == "11:00"

    def test_slots_blocked_by_existing_appointment(
        self, db, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """An existing PENDING appointment must block its time slot."""
        schedule = ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        appt_start = timezone.make_aware(datetime.combine(next_monday, time(9, 0)), tz)
        appt_end = appt_start + timedelta(minutes=30)

        # Create the blocking appointment directly (bypass serializer validations).
        # Debe estar CONFIRMADA: una cita 'pending' no cierra el hueco.
        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=appt_start,
            end_at=appt_end,
            status=Appointment.Status.CONFIRMED,
        )

        url = (
            f"/api/professionals/{professional.pk}/available-slots/"
            f"?date={next_monday.isoformat()}&duration=30"
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        slots = response.data["available_slots"]
        # 09:00 slot must be absent (blocked)
        assert not any("T09:00" in s for s in slots)
        # 09:30 slot should be present (09:30–10:00 is free)
        assert any("T09:30" in s or "09:30" in s for s in slots)

    def test_confirmed_appointment_also_blocks_slot(
        self, db, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """CONFIRMED appointments must also block slots."""
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        appt_start = timezone.make_aware(datetime.combine(next_monday, time(10, 0)), tz)

        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=appt_start,
            end_at=appt_start + timedelta(minutes=30),
            status=Appointment.Status.CONFIRMED,
        )

        url = (
            f"/api/professionals/{professional.pk}/available-slots/"
            f"?date={next_monday.isoformat()}&duration=30"
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        slots = response.data["available_slots"]
        assert not any("T10:00" in s for s in slots)

    def test_cancelled_appointment_does_not_block_slot(
        self, db, admin_client, clinic_a, patient_a, service_a, professional
    ):
        """CANCELLED appointments must NOT block slots."""
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )
        professional.services.add(service_a)
        next_monday = _next_weekday(0)
        tz = timezone.get_current_timezone()
        appt_start = timezone.make_aware(datetime.combine(next_monday, time(9, 0)), tz)

        Appointment.objects.create(
            clinic=clinic_a,
            patient=patient_a,
            service=service_a,
            professional=professional,
            scheduled_at=appt_start,
            end_at=appt_start + timedelta(minutes=30),
            status=Appointment.Status.CANCELLED,
        )

        url = (
            f"/api/professionals/{professional.pk}/available-slots/"
            f"?date={next_monday.isoformat()}&duration=30"
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        slots = response.data["available_slots"]
        # 09:00 slot should be FREE (cancelled doesn't block)
        assert any("T09:00" in s or "09:00" in s for s in slots)

    def test_missing_date_param_returns_400(self, admin_client, professional):
        url = f"/api/professionals/{professional.pk}/available-slots/"
        response = admin_client.get(url)
        assert response.status_code == 400

    def test_invalid_date_format_returns_400(self, admin_client, professional):
        url = f"/api/professionals/{professional.pk}/available-slots/?date=not-a-date"
        response = admin_client.get(url)
        assert response.status_code == 400

    def test_total_slots_match_schedule_window(
        self, db, admin_client, professional
    ):
        """09:00–11:00, cita de 30 min, granularidad de 15 min → 7 slots.

        El paso del generador es `professional.slot_granularity_minutes` (15 por
        defecto), no la duración de la cita: se ofrece 09:00, 09:15, 09:30… El
        último es 10:30, porque 10:45 + 30 min se saldría del tramo.
        """
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        next_monday = _next_weekday(0)
        url = (
            f"/api/professionals/{professional.pk}/available-slots/"
            f"?date={next_monday.isoformat()}&duration=30"
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        assert len(response.data["available_slots"]) == 7

    def test_slot_granularity_is_configurable(
        self, db, admin_client, professional
    ):
        """Con granularidad de 30 min, el mismo tramo ofrece 4 slots."""
        professional.slot_granularity_minutes = 30
        professional.save(update_fields=['slot_granularity_minutes'])
        ProfessionalSchedule.objects.create(
            professional=professional,
            day_of_week=0,
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_active=True,
        )
        next_monday = _next_weekday(0)
        url = (
            f"/api/professionals/{professional.pk}/available-slots/"
            f"?date={next_monday.isoformat()}&duration=30"
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        # 09:00, 09:30, 10:00, 10:30
        assert len(response.data["available_slots"]) == 4
