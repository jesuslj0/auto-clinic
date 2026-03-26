"""Tests for core models: Clinic and User."""
import pytest

from core.models import Clinic, User


@pytest.mark.django_db
class TestClinicModel:
    def test_create_clinic(self, clinic_a):
        assert clinic_a.pk == "clinic-alpha"
        assert clinic_a.clinic_id == "clinic-alpha"
        assert clinic_a.name == "Clinic Alpha"

    def test_str_representation(self, clinic_a):
        assert str(clinic_a) == "Clinic Alpha"

    def test_clinic_id_is_primary_key(self, clinic_a):
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            Clinic.objects.create(clinic_id="clinic-alpha", name="Duplicate")

    def test_ordering_by_name(self, db):
        Clinic.objects.create(clinic_id="zebra-clinic", name="Zebra Clinic")
        Clinic.objects.create(clinic_id="alpha-clinic", name="Alpha Clinic")
        names = list(Clinic.objects.order_by("name").values_list("name", flat=True))
        assert names == sorted(names)

    def test_clinic_has_timezone_field(self, clinic_a):
        assert hasattr(clinic_a, "timezone")
        assert clinic_a.timezone == "Europe/Madrid"


@pytest.mark.django_db
class TestUserModel:
    def test_create_user(self, admin_user):
        assert admin_user.pk is not None
        assert admin_user.email == "admin@alpha.test"
        assert admin_user.role == User.Role.ADMIN
        assert admin_user.username == admin_user.email

    def test_username_mirrors_email(self, db, clinic_a):
        user = User.objects.create_user(
            email="mirror@test.com",
            password="pass",
            clinic=clinic_a,
        )
        assert user.username == "mirror@test.com"

    def test_email_as_login_field(self):
        assert User.USERNAME_FIELD == "email"

    def test_staff_role_default(self, db, clinic_a):
        user = User.objects.create_user(
            email="default@test.com",
            password="pass",
            clinic=clinic_a,
        )
        assert user.role == User.Role.STAFF

    def test_superuser_creation(self, superuser):
        assert superuser.is_superuser is True
        assert superuser.is_staff is True
        assert superuser.role == User.Role.ADMIN

    def test_create_user_without_email_raises(self, db):
        with pytest.raises(ValueError, match="Email is required"):
            User.objects.create_user(email="", password="pass")

    def test_str_representation(self, admin_user):
        assert str(admin_user) == "admin@alpha.test"

    def test_user_belongs_to_clinic(self, admin_user, clinic_a):
        assert admin_user.clinic == clinic_a

    def test_ordering_by_email(self, db, clinic_a):
        User.objects.create_user(email="z@test.com", password="p", clinic=clinic_a)
        User.objects.create_user(email="a@test.com", password="p", clinic=clinic_a)
        emails = list(User.objects.filter(clinic=clinic_a).values_list("email", flat=True))
        assert emails == sorted(emails)
