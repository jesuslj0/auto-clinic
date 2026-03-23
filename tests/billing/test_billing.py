"""Tests for billing app: Subscription model and API."""
from datetime import timedelta

import pytest
from django.utils import timezone

from billing.models import Subscription


@pytest.mark.django_db
class TestSubscriptionModel:
    def test_create_subscription(self, subscription_a):
        assert subscription_a.pk is not None
        assert subscription_a.status == Subscription.Status.ACTIVE
        assert subscription_a.plan_name == "Basic"

    def test_str_representation(self, subscription_a, clinic_a):
        assert clinic_a.name in str(subscription_a)
        assert "Basic" in str(subscription_a)

    def test_default_status_is_trial(self, db, clinic_a):
        sub = Subscription.objects.create(
            clinic=clinic_a,
            plan_name="Trial Plan",
            starts_at=timezone.now(),
        )
        assert sub.status == Subscription.Status.TRIAL

    def test_status_choices(self):
        choices = {c[0] for c in Subscription.Status.choices}
        assert {"trial", "active", "past_due", "cancelled"} == choices


@pytest.mark.django_db
class TestSubscriptionViewSet:
    def test_list_subscriptions_scoped_to_clinic(self, admin_client, subscription_a, db, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b,
            plan_name="Pro",
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )
        response = admin_client.get("/api/subscriptions/")
        assert response.status_code == 200
        ids = [s["id"] for s in response.data["results"]]
        assert subscription_a.pk in ids
        assert sub_b.pk not in ids

    def test_staff_can_read_subscriptions(self, staff_client, subscription_a):
        response = staff_client.get("/api/subscriptions/")
        assert response.status_code == 200

    def test_staff_cannot_create_subscription(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "plan_name": "Staff Plan",
            "status": "trial",
            "starts_at": timezone.now().isoformat(),
        }
        response = staff_client.post("/api/subscriptions/", data)
        assert response.status_code == 403

    def test_admin_can_create_subscription(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "plan_name": "Premium",
            "status": "active",
            "starts_at": timezone.now().isoformat(),
        }
        response = admin_client.post("/api/subscriptions/", data)
        assert response.status_code == 201

    def test_retrieve_other_clinic_subscription_not_found(self, admin_client, db, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b,
            plan_name="Other",
            starts_at=timezone.now(),
        )
        response = admin_client.get(f"/api/subscriptions/{sub_b.pk}/")
        assert response.status_code == 404

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/subscriptions/")
        assert response.status_code in (401, 403)

    def test_superuser_sees_all_subscriptions(self, superuser_client, subscription_a, db, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b,
            plan_name="Pro",
            starts_at=timezone.now(),
        )
        response = superuser_client.get("/api/subscriptions/")
        assert response.status_code == 200
        ids = [s["id"] for s in response.data["results"]]
        assert subscription_a.pk in ids
        assert sub_b.pk in ids
